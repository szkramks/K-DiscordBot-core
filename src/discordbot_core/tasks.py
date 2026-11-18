"""見回り（tasks.loop）の共通の形: つながってから 1 回だけ始める・サーバーごとに回して例外はそのサーバーだけで捕まえる・見回りの本体の例外で止めない（guarded）・1 日 1 回の掃除"""
import functools
import logging
from datetime import date, datetime
from typing import Awaitable, Callable, Iterable

import discord
from discord.ext import commands, tasks

from . import health


def guarded(log: logging.Logger, label: str):
    """見回りの本体（async の関数）を包む。@tasks.loop の下に付ける: @tasks.loop(minutes=1) / @guarded(log, "auto") / async def watch(self)。
    本体が例外（DB のロックなど）を投げても、log に「<label> watch failed」と例外を残してその回だけ飛ばす。
    discord.py の tasks.loop は、例外が外に出るとループごと止まり、BOT を作り直すまで戻らないため。
    止めたいとき（cancel）の CancelledError は Exception ではないので、そのまま通す。
    最後まで動けた回だけ、health.WATCHES に「動けた」と記録する（失敗した回は進めない。長く記録が進まないと、
    合図が止まって unhealthy になり、さらに長いと BOT が自分で終了する。health.py）"""
    def decorate(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                result = await fn(*args, **kwargs)
            except Exception:
                log.exception("%s watch failed", label)
                return None
            health.WATCHES.ok(label)
            return result
        wrapper.watch_label = label
        return wrapper
    return decorate


def start_once(loop: tasks.Loop) -> None:
    """on_ready から呼ぶ。Discord につながない --check では始めず、再接続のたびの on_ready では重ねて始めない。
    guarded を付けた見回りは、始めるときに health.WATCHES に（名前と間隔を）登録する。登録の時刻が最初の基準"""
    if loop.is_running():
        return
    label = getattr(getattr(loop, "coro", None), "watch_label", None)
    interval = (loop.seconds or 0) + (loop.minutes or 0) * 60 + (loop.hours or 0) * 3600 if label else 0
    if label and interval > 0:                      # time= で動く見回りは間隔がないので見張らない
        health.WATCHES.register(label, interval)
    loop.start()


async def for_each_guild(bot: commands.Bot, guild_ids: Iterable[int], work: Callable[[discord.Guild], Awaitable[None]],
                         log: logging.Logger, label: str) -> None:
    """サーバーごとに work(guild) を呼ぶ。BOT が入っていないサーバーは飛ばす。
    1 つのサーバーの失敗でほかのサーバーを止めない（log に「<label> failed」と例外を残す）"""
    for guild_id in guild_ids:
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue
        try:
            await work(guild)
        except Exception:
            log.exception("%s failed", label)


class Daily:
    """1 日 1 回だけ行うこと（古い記録の掃除など）。due(now) は日付が変わって最初の 1 回だけ True"""

    def __init__(self):
        self._on: date | None = None

    def due(self, now: datetime) -> bool:
        if self._on == now.date():
            return False
        self._on = now.date()
        return True

    async def purge_once(self, now: datetime, purge: Callable[[], Awaitable[int]], log: logging.Logger, label: str) -> None:
        """日付が変わって最初の 1 回だけ purge() を呼び、消した数が 0 でなければ log に「purged N old <label> records」を残す。
        purge は引数なしの非同期の関数で、消した数を返す（何を・何日分残すかは呼ぶ側が決める）"""
        if self.due(now) and (n := await purge()):
            log.info("purged %d old %s records", n, label)
