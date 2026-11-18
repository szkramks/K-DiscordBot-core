"""core/tasks.py: 見回りの開始は 1 回だけ・サーバーごとの例外はそのサーバーだけ・1 日 1 回"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from discordbot_core.tasks import Daily, for_each_guild, guarded, start_once


def arun(coro):
    return asyncio.run(coro)


class FakeLoop:
    def __init__(self):
        self.started = 0
        self._running = False

    def is_running(self):
        return self._running

    def start(self):
        self.started += 1
        self._running = True


def test_start_once_starts_only_when_not_running():
    loop = FakeLoop()
    start_once(loop)
    start_once(loop)          # 再接続のたびの on_ready でも重ねて始めない
    assert loop.started == 1


def test_for_each_guild_skips_missing_and_isolates_failures(caplog):
    guilds = {1: SimpleNamespace(id=1), 3: SimpleNamespace(id=3)}
    bot = SimpleNamespace(get_guild=guilds.get)
    seen = []

    async def work(guild):
        if guild.id == 1:
            raise RuntimeError("boom")
        seen.append(guild.id)
    log = logging.getLogger("test.tasks")
    with caplog.at_level(logging.ERROR, logger="test.tasks"):
        arun(for_each_guild(bot, [1, 2, 3], work, log, "remind"))
    assert seen == [3]                                    # 2 は BOT が入っていない、1 の失敗で 3 は止まらない
    assert [r.getMessage() for r in caplog.records] == ["remind failed"]
    assert caplog.records[0].exc_info is not None         # 例外の中身もログに残る


def test_daily_due_once_per_day():
    d = Daily()
    now = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)
    assert d.due(now) is True
    assert d.due(now + timedelta(hours=5)) is False
    assert d.due(now + timedelta(days=1)) is True


def test_daily_purge_once_calls_once_a_day_and_logs_only_when_something_was_removed(caplog):
    d = Daily()
    log = logging.getLogger("test.tasks")
    now = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)
    calls = []

    def purge_returning(n):
        async def purge():
            calls.append(n)
            return n
        return purge
    with caplog.at_level(logging.INFO, logger="test.tasks"):
        arun(d.purge_once(now, purge_returning(3), log, "remind"))
        arun(d.purge_once(now + timedelta(hours=5), purge_returning(9), log, "remind"))      # 同じ日は呼ばない
        arun(d.purge_once(now + timedelta(days=1), purge_returning(0), log, "remind"))       # 0 件ならログに出さない
        arun(d.purge_once(now + timedelta(days=2), purge_returning(1), log, "auto"))
    assert calls == [3, 0, 1]
    assert [r.getMessage() for r in caplog.records] == ["purged 3 old remind records", "purged 1 old auto records"]


def test_guarded_swallows_exceptions_and_runs_again(caplog):
    import sqlite3
    log = logging.getLogger("test.tasks")
    calls = []

    @guarded(log, "auto")
    async def watch(n):
        calls.append(n)
        if n == 1:
            raise sqlite3.OperationalError("database is locked")
        return n
    with caplog.at_level(logging.ERROR, logger="test.tasks"):
        assert arun(watch(1)) is None                       # 例外は外に出ない
        assert arun(watch(2)) == 2                          # 次の回はふつうに動く
    assert calls == [1, 2]
    assert [r.getMessage() for r in caplog.records] == ["auto watch failed"]
    assert caplog.records[0].exc_info is not None


def test_guarded_lets_cancel_through():
    log = logging.getLogger("test.tasks")

    @guarded(log, "auto")
    async def watch():
        raise asyncio.CancelledError()
    try:
        arun(watch())
    except asyncio.CancelledError:
        return
    raise AssertionError("CancelledError は止めない")
