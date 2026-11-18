"""core/store.py: MIGRATIONS を古い版から当てる・@threaded・日時の変換"""
import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from discordbot_core.store import DT_FMT, JST, NOW_SQL, BaseStore, fmt_dt, parse_dt, threaded


class TinyStore(BaseStore):
    MIGRATIONS = [
        "CREATE TABLE a (id INTEGER PRIMARY KEY, name TEXT)",
        "ALTER TABLE a ADD COLUMN note TEXT",
        "CREATE TABLE b (id INTEGER PRIMARY KEY)",
    ]

    @threaded
    def put(self, name: str) -> int:
        self.thread = threading.get_ident()
        with self._connect() as con:
            return con.execute("INSERT INTO a (name) VALUES (?)", (name,)).lastrowid

    @threaded
    def names(self) -> list[str]:
        with self._connect() as con:
            return [r["name"] for r in con.execute("SELECT name FROM a ORDER BY id")]


def version(path) -> int:
    with sqlite3.connect(path) as con:
        return con.execute("PRAGMA user_version").fetchone()[0]


def test_migrations_from_empty(tmp_path):
    p = tmp_path / "t.db"
    TinyStore(p)
    assert version(p) == len(TinyStore.MIGRATIONS)
    with sqlite3.connect(p) as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(a)")]
    assert cols == ["id", "name", "note"]


def test_migrations_from_old_version(tmp_path):
    p = tmp_path / "t.db"
    with sqlite3.connect(p) as con:
        con.execute(TinyStore.MIGRATIONS[0])
        con.execute("INSERT INTO a (name) VALUES ('old')")
        con.execute("PRAGMA user_version = 1")
    TinyStore(p)                                   # 2 と 3 だけが当たり、行は残る
    assert version(p) == 3
    with sqlite3.connect(p) as con:
        assert con.execute("SELECT name, note FROM a").fetchall() == [("old", None)]
        assert con.execute("SELECT COUNT(*) FROM b").fetchone()[0] == 0
    TinyStore(p)                                   # もう一度開いても何もしない
    assert version(p) == 3


def test_threaded_runs_in_another_thread_and_returns_value(tmp_path):
    st = TinyStore(tmp_path / "t.db")

    async def go():
        row = await st.put("x")
        return row, await st.names()
    row, names = asyncio.run(go())
    assert row == 1 and names == ["x"]
    assert st.thread != threading.get_ident()      # 同期の本体は別のスレッドで動く
    assert TinyStore.put.__name__ == "put"         # 名前と docstring は本体のまま（補完のため）


def test_dt_roundtrip():
    """日時は JST の DT_FMT で持つ"""
    dt = datetime(2026, 11, 2, 12, 34, 56, tzinfo=JST)
    assert fmt_dt(dt) == "2026-11-02 12:34:56" == dt.strftime(DT_FMT)
    assert parse_dt(fmt_dt(dt)) == dt and parse_dt(fmt_dt(dt)).tzinfo == JST
    assert parse_dt(None) is None and fmt_dt(None) is None


def test_fmt_dt_converts_utc_to_jst():
    utc = datetime(2026, 11, 2, 12, 34, 56, tzinfo=timezone.utc)
    assert fmt_dt(utc) == "2026-11-02 21:34:56"
    assert parse_dt("2026-11-02 21:34:56") == utc          # 同じ時刻（タイムゾーン付きの比較）
    assert fmt_dt(datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)) == "2026-11-03 00:00:00"    # 日付をまたぐ


def test_now_sql_is_jst_now(tmp_path):
    import sqlite3
    con = sqlite3.connect(tmp_path / "x.db")
    got = parse_dt(con.execute(f"SELECT {NOW_SQL}").fetchone()[0])
    assert abs(got - datetime.now(timezone.utc)) < timedelta(minutes=1)


def test_connect_waits_for_a_locked_writer(tmp_path):
    """別の接続が書き込みを持っていても、既定の 5 秒で諦めず BUSY_TIMEOUT まで待つ"""
    import time
    from discordbot_core.store import BUSY_TIMEOUT
    assert BUSY_TIMEOUT >= 30
    st = TinyStore(tmp_path / "t.db")
    holder = sqlite3.connect(tmp_path / "t.db", isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN EXCLUSIVE")
    threading.Timer(1.0, lambda: holder.execute("COMMIT")).start()
    started = time.monotonic()
    assert asyncio.run(st.names()) == []                    # 1 秒後にロックが外れるまで待って成功する
    assert 0.5 < time.monotonic() - started < 10
