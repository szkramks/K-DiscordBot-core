"""SQLite の土台: 接続・MIGRATIONS を当てる・同期の処理を別のスレッドで動かす async の包み

- BOT 側は BaseStore を継承し、MIGRATIONS（CREATE TABLE / ALTER TABLE の並び）を持たせる。表を足す・変えるときは末尾に足す
  （並びは変えない。PRAGMA user_version で、どこまで当てたかを覚える）。
- 読み書きのメソッドは同期の本体（sqlite3）を書き、@threaded を付ける。呼ぶ側は await で使う（中では別のスレッドで動かす）。
"""
import asyncio
import functools
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Concatenate, Coroutine, ParamSpec, TypeVar
from zoneinfo import ZoneInfo

DB_PATH = Path(os.getenv("DATA_DIR", "/data")) / "bot.db"
BUSY_TIMEOUT = 30              # 別の接続の書き込みを待つ秒数。既定の 5 秒だと、ストレージが数秒遅れたときに database is locked になる
DT_FMT = "%Y-%m-%d %H:%M:%S"   # 日時は JST のこの形で持つ（オフセットなし）
JST = ZoneInfo("Asia/Tokyo")
NOW_SQL = "datetime('now', '+9 hours')"   # SQL の「今」（JST の DT_FMT）。'localtime' は TZ に依存するので使わない。DEFAULT (datetime('now')) は UTC なので頼らない

P = ParamSpec("P")
R = TypeVar("R")
S = TypeVar("S", bound="BaseStore")


def threaded(fn: Callable[Concatenate[S, P], R]) -> Callable[Concatenate[S, P], Coroutine[Any, Any, R]]:
    """同期の本体を async にする（asyncio.to_thread）。型は本体のままなので、エディターの補完が効く"""
    @functools.wraps(fn)
    async def wrapper(self: S, *args: P.args, **kwargs: P.kwargs) -> R:
        return await asyncio.to_thread(fn, self, *args, **kwargs)
    return wrapper


def parse_dt(s: str | None) -> datetime | None:
    """DT_FMT の文字列 → JST の datetime（None はそのまま）"""
    return datetime.strptime(s, DT_FMT).replace(tzinfo=JST) if s else None


def fmt_dt(dt: datetime | None) -> str | None:
    """datetime（タイムゾーン付き）→ DT_FMT の文字列（JST。None はそのまま）"""
    return dt.astimezone(JST).strftime(DT_FMT) if dt else None


class BaseStore:
    MIGRATIONS: list[str] = []   # BOT 側で上書きする

    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT)
        con.row_factory = sqlite3.Row
        return con

    def _migrate(self) -> None:
        with closing(self._connect()) as con, con:
            done = con.execute("PRAGMA user_version").fetchone()[0]
            for version, sql in enumerate(self.MIGRATIONS[done:], start=done + 1):
                con.execute(sql)
                con.execute(f"PRAGMA user_version = {version}")
