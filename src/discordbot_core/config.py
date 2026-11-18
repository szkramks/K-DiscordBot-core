"""設定（.env / 環境変数）の読み方の共通部分。項目は BOT ごとの bot/config.py で決める（Settings の dataclass）"""
import os

from dotenv import load_dotenv


def load_env() -> None:
    load_dotenv()  # コンテナでは env_file で渡すので何もしない（コンテナの外で動かすとき用）


def require(name: str) -> str:
    """必ず要る項目。なければ起動を止める（値はログに出さない）"""
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"環境変数がありません: {name}")
    return value


def optional(name: str, default: str | None = None) -> str | None:
    """省ける項目。空なら default"""
    return os.getenv(name) or default
