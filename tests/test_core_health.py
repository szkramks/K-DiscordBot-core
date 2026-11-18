"""core/health.py: 見回りの生死の見張り（遅れで合図を止める・長く止まったら終了）と、guarded・start_once との組み合わせ"""
import asyncio
import logging

import pytest

from discordbot_core import health
from discordbot_core.client import BaseBot
from discordbot_core.health import Watches, tick
from discordbot_core.tasks import guarded, start_once

M = 60.0


def arun(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    w = Watches()
    monkeypatch.setattr(health, "WATCHES", w)
    monkeypatch.setattr(health, "ALIVE", tmp_path / "alive")
    return w


class FakeBot:
    def __init__(self, ready=True, latency=0.1):
        self.ready, self._latency = ready, latency
        self.exit_code = 0
        self.closed = False

    def is_ready(self):
        return self.ready

    def is_closed(self):
        return self.closed

    @property
    def latency(self):
        return self._latency

    async def close(self):
        self.closed = True


def test_registered_just_now_is_not_stale(fresh):
    fresh.register("auto", 60, now=1000)
    assert fresh.check(now=1000) == ([], [])
    assert fresh.check(now=1000 + 5 * M) == ([], [])             # ちょうど 5 分はまだ遅れではない


def test_ok_moves_time_but_only_when_called(fresh):
    fresh.register("auto", 60, now=0)
    fresh.ok("auto", now=4 * M)
    assert fresh.check(now=8 * M) == ([], [])                    # 最後に動けたのは 4 分 → 4 分前
    fresh.ok("nobody", now=0)                                    # 登録していない名前は無視


def test_one_minute_watch_stale_after_5_min_and_dead_after_10(fresh):
    fresh.register("auto", 60, now=0)
    stale, dead = fresh.check(now=5 * M + 1)
    assert [(n, w) for n, _, w in stale] == [("auto", True)] and dead == []
    stale, dead = fresh.check(now=10 * M)
    assert dead == []                                            # ちょうど 10 分はまだ
    stale, dead = fresh.check(now=10 * M + 1)
    assert [n for n, _ in dead] == ["auto"]


def test_fifteen_minute_watch_thresholds(fresh):
    fresh.register("sheet", 15 * M, now=0)
    assert fresh.check(now=75 * M) == ([], [])                   # 75 分までは遅れではない
    stale, dead = fresh.check(now=75 * M + 1)
    assert [n for n, _, _ in stale] == ["sheet"] and dead == []
    assert fresh.check(now=90 * M)[1] == []
    assert [n for n, _ in fresh.check(now=90 * M + 1)[1]] == ["sheet"]


def test_warning_once_per_5_minutes(fresh):
    fresh.register("auto", 60, now=0)
    assert fresh.check(now=6 * M)[0][0][2] is True
    assert fresh.check(now=7 * M)[0][0][2] is False
    assert fresh.check(now=10 * M)[0][0][2] is False
    assert fresh.check(now=11 * M)[0][0][2] is True              # 前の WARNING から 5 分
    fresh.ok("auto", now=11 * M)
    assert fresh.check(now=11 * M)[0] == []                      # 戻ったら印も消える
    assert fresh.check(now=17 * M)[0][0][2] is True


def test_reregister_does_not_reset_time(fresh):
    fresh.register("auto", 60, now=0)
    fresh.register("auto", 60, now=9 * M)
    assert fresh.check(now=9 * M)[0] != []


def test_tick_writes_alive_only_when_connected_and_not_stale(fresh):
    fresh.register("auto", 60, now=0)
    bot = FakeBot()
    arun(tick(bot, now=M))
    assert health.ALIVE.exists()
    health.ALIVE.unlink()
    arun(tick(FakeBot(ready=False), now=M))                      # つながっていない → 今と同じく書かない
    arun(tick(FakeBot(latency=float("inf")), now=M))
    assert not health.ALIVE.exists()
    arun(tick(bot, now=6 * M))                                   # 遅れ → 書かない
    assert not health.ALIVE.exists() and bot.exit_code == 0 and not bot.closed


def test_tick_logs_warning_and_exits_when_dead(fresh, caplog):
    fresh.register("auto", 60, now=0)
    bot = FakeBot()
    with caplog.at_level(logging.WARNING, logger="discordbot_core.health"):
        arun(tick(bot, now=6 * M))
        arun(tick(bot, now=7 * M))                               # 5 分に 1 回
        assert bot.closed is False
        arun(tick(bot, now=10 * M + 1))
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs == ["watch stale: auto 6m", "watch stale too long, exiting for restart: auto 10m"]
    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.ERROR]
    assert bot.closed and bot.exit_code == 1 and not health.ALIVE.exists()


def test_no_watches_means_old_behavior(fresh):
    arun(tick(FakeBot(), now=10**6))                             # --check や見回りなし: 何も登録されず、終了しない
    assert health.ALIVE.exists()


def test_guarded_records_only_successful_runs(fresh):
    log = logging.getLogger("test.health")
    fresh.register("auto", 60, now=0)
    state = {"fail": True}

    @guarded(log, "auto")
    async def watch():
        if state["fail"]:
            raise RuntimeError("boom")

    arun(watch())                                                # 失敗した回は進まない
    assert fresh._items["auto"]["last_ok"] == 0
    state["fail"] = False
    arun(watch())
    assert fresh._items["auto"]["last_ok"] > 0
    assert watch.watch_label == "auto"


def test_start_once_registers_guarded_loops_with_their_interval(fresh):
    log = logging.getLogger("test.health")

    class FakeLoop:
        def __init__(self, coro, minutes=None, seconds=None, hours=None):
            self.coro, self.minutes, self.seconds, self.hours = coro, minutes, seconds, hours
            self.started = 0

        def is_running(self):
            return self.started > 0

        def start(self):
            self.started += 1

    @guarded(log, "sheet")
    async def sheet():
        pass

    async def plain():
        pass

    start_once(FakeLoop(sheet, minutes=15))
    start_once(FakeLoop(plain, minutes=1))                       # guarded でない → 見張らない
    start_once(FakeLoop(sheet, minutes=None))                    # 間隔がない（time= の見回り）→ 見張らない
    assert {n: i["interval"] for n, i in fresh._items.items()} == {"sheet": 900}
    again = FakeLoop(sheet, minutes=15)
    start_once(again)
    start_once(again)                                            # 重ねて始めない
    assert again.started == 1


def test_start_once_works_with_real_tasks_loop(fresh):
    from discord.ext import tasks
    log = logging.getLogger("test.health")

    async def go():
        @tasks.loop(minutes=1)
        @guarded(log, "auto")
        async def watch():
            pass
        start_once(watch)
        watch.cancel()
    arun(go())
    assert fresh._items["auto"]["interval"] == 60


def test_run_exits_with_exit_code(monkeypatch):
    monkeypatch.setattr("discord.ext.commands.Bot.run", lambda self, *a, **k: None)
    bot = BaseBot.__new__(BaseBot)               # __init__ は Discord の設定が要るので通さない
    bot.exit_code = 1
    with pytest.raises(SystemExit) as e:
        BaseBot.run(bot, "token")
    assert e.value.code == 1
    bot.exit_code = 0
    BaseBot.run(bot, "token")                                    # 0 なら何もしない
