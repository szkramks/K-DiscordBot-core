"""ヘルスチェック用の合図と、見回りの生死の見張り。

- 合図: Discord につながっていて、登録した見回りがどれも遅れていない間だけ、1 分ごとに /tmp/alive を書く（Dockerfile の HEALTHCHECK が見る）。
- 見張り: 見回りごとに「最後に最後まで動けた時刻」をメモリーだけに持つ（DB・ファイルには書かない）。tasks.guarded が成功のたびに記録し、
  tasks.start_once が見回りを始めるときに登録する。
  - 遅れ: 最後に動けてから 間隔 × 5 を過ぎた → 合図を書かない（3 分で unhealthy）。`watch stale: <名前> <経過>m` を WARNING（同じ見回りでは 5 分に 1 回まで）。
  - 作り直し: 最後に動けてから 10 分か 間隔 × 6 の長いほうを過ぎた → `watch stale too long, exiting for restart: <名前> <経過>m` を ERROR で残し、
    BOT を閉じて 0 以外で終了する（Docker の restart: always が作り直す）。
"""
import logging
import time
from pathlib import Path

from discord.ext import commands, tasks

log = logging.getLogger(__name__)

ALIVE = Path("/tmp/alive")
STALE_FACTOR = 5              # 遅れ: 間隔 × 5
RESTART_FACTOR = 6            # 作り直し: 間隔 × 6 か RESTART_MIN_S の長いほう
RESTART_MIN_S = 600
WARN_EVERY_S = 300            # 同じ見回りの WARNING は 5 分に 1 回まで


class Watches:
    """見回りの最後に動けた時刻。時計は単調増加の time.monotonic（壁時計のずれに影響されない）。now は試験から差し込む"""

    def __init__(self):
        self._items: dict[str, dict] = {}

    def register(self, name: str, interval_s: float, now: float | None = None) -> None:
        """登録の時刻を最初の基準にする（起動直後に古い扱いにしない）。同じ名前の再登録は時刻を動かさない"""
        if name in self._items:
            return
        self._items[name] = {"interval": interval_s, "last_ok": time.monotonic() if now is None else now, "warned": None}

    def ok(self, name: str, now: float | None = None) -> None:
        """最後まで動けた回の記録。登録していない名前は無視する"""
        if (item := self._items.get(name)) is not None:
            item["last_ok"] = time.monotonic() if now is None else now

    def check(self, now: float | None = None) -> tuple[list[tuple[str, float, bool]], list[tuple[str, float]]]:
        """(遅れている見回り, 作り直す見回り) を返す。
        遅れ: (名前, 経過秒, ログに出すか)。ログに出すのは、前回の WARNING から 5 分たったものだけ。
        作り直し: (名前, 経過秒)"""
        now = time.monotonic() if now is None else now
        stale, dead = [], []
        for name, item in self._items.items():
            elapsed = now - item["last_ok"]
            interval = item["interval"]
            if elapsed > max(RESTART_MIN_S, interval * RESTART_FACTOR):
                dead.append((name, elapsed))
            if elapsed > interval * STALE_FACTOR:
                warn = item["warned"] is None or now - item["warned"] >= WARN_EVERY_S
                if warn:
                    item["warned"] = now
                stale.append((name, elapsed, warn))
            else:
                item["warned"] = None
        return stale, dead


WATCHES = Watches()


async def tick(bot: commands.Bot, now: float | None = None) -> None:
    """1 分ごとの判定（start の alive が呼ぶ。試験から now を差し込む）"""
    stale, dead = WATCHES.check(now)
    for name, elapsed, warn in stale:
        if warn:
            log.warning("watch stale: %s %dm", name, elapsed // 60)
    if dead:
        for name, elapsed in dead:
            log.error("watch stale too long, exiting for restart: %s %dm", name, elapsed // 60)
        bot.exit_code = 1
        await bot.close()
        return
    # 切れている間は latency が inf になる。discord.py は自分でつなぎ直すので、短い切断では unhealthy にならない
    if bot.is_ready() and not bot.is_closed() and bot.latency < 60 and not stale:
        ALIVE.touch()


def start(bot: commands.Bot) -> None:
    @tasks.loop(seconds=60)
    async def alive():
        await tick(bot)

    @alive.before_loop
    async def wait_ready():
        # 1 回目をつながった直後にする（起動から healthy までを短くする）
        await bot.wait_until_ready()

    alive.start()
