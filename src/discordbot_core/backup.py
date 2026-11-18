"""DB を SQL の文として書き出す（定期のバックアップから呼ぶ）

使う側で dump(DB_PATH) を呼ぶモジュールを用意する（例: python -m bot.backup > bot.sql）
戻すときは、BOT を止めて空の DB に流す。表の版（PRAGMA user_version）も最後に書く。
"""
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import TextIO


def dump(db_path: Path, out: TextIO = sys.stdout) -> None:
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as con:
        for line in con.iterdump():
            out.write(line + "\n")
        version = con.execute("PRAGMA user_version").fetchone()[0]
        out.write(f"PRAGMA user_version = {version};\n")
