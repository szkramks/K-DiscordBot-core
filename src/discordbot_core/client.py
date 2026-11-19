"""BOT 本体の土台: 機能の自動読み込み・コマンドの登録・エラーの返し方・デプロイ前の検証（--check）

BOT 側は BaseBot を継承し、features（bot.features のパッケージ）と store を渡す。
sync の前後に BOT ごとの処理が要れば before_load / before_sync / after_sync を上書きする。
"""
import asyncio
import logging
import pkgutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Callable

import discord
from discord import app_commands
from discord.ext import commands

from . import health

log = logging.getLogger(__name__)

NO_PERMISSION = "⚠️ このコマンドを使う権限がありません。"
FORBIDDEN = "⚠️ BOT に権限が足りません。サーバーの設定で BOT のロールの権限を確認してください。"
FAILED = "⚠️ 処理に失敗しました。時間をおいてもう一度試してください。"


def extensions(package: ModuleType) -> list[str]:
    """package の下の機能（extension）の名前。_ で始まるものは除く（作りかけを置いておける）。
    フォルダー（features/<機能>/）なら、その中の cog モジュール。ファイル（cogs/<機能>.py）ならそのモジュール"""
    found = []
    for m in pkgutil.iter_modules(package.__path__):
        if m.name.startswith("_"):
            continue
        if m.ispkg:
            if (Path(m.module_finder.path) / m.name / "cog.py").exists():
                found.append(f"{package.__name__}.{m.name}.cog")
        else:
            found.append(f"{package.__name__}.{m.name}")
    return found


async def on_app_command_error(inter: discord.Interaction, error: app_commands.AppCommandError) -> None:
    """どの機能のエラーもここで返す（機能ごとの説明は、各 cog の中で ValueError などとして先に返す）"""
    cause = getattr(error, "original", error)
    if isinstance(error, app_commands.CheckFailure):
        msg = NO_PERMISSION
    elif isinstance(cause, discord.Forbidden):
        msg = FORBIDDEN
    else:
        log.exception("command error: %s", inter.command and inter.command.qualified_name, exc_info=error)
        msg = FAILED
    if inter.response.is_done():
        await inter.followup.send(msg, ephemeral=True)
    else:
        await inter.response.send_message(msg, ephemeral=True)


class BaseBot(commands.Bot):
    def __init__(self, *, features: ModuleType, store, intents: discord.Intents | None = None,
                 allowed_installs: app_commands.AppInstallationType | None = None,
                 allowed_contexts: app_commands.AppCommandContext | None = None, **kwargs):
        # 既定: サーバーに入れた BOT（Guild Install）で、サーバーの中で使う。指定しないと、登録した時点の Developer Portal の
        # Installation の設定が使われ、User Install だけだとサーバーに出ない。特権インテントは BOT 側で intents に足す
        super().__init__(command_prefix=commands.when_mentioned,
                         intents=intents if intents is not None else discord.Intents.default(), help_command=None,
                         allowed_mentions=discord.AllowedMentions.none(),  # 文に @everyone などがあっても鳴らさない
                         allowed_installs=allowed_installs or app_commands.AppInstallationType(guild=True, user=False),
                         allowed_contexts=allowed_contexts or app_commands.AppCommandContext(guild=True), **kwargs)
        self.features = features
        self.exit_code = 0          # 0 以外にして close() すると、run() が終わったあとその番号で終了する（health.py の作り直し）
        # サーバーごとの設定など。cogs は self.bot.store を使う（読み込みより前に作っておく）
        self.store = store
        self.tree.error(self.app_command_error)

    def run(self, *args, **kwargs) -> None:
        super().run(*args, **kwargs)
        if self.exit_code:
            sys.exit(self.exit_code)

    def extension_names(self) -> list[str]:
        return extensions(self.features)

    async def setup_hook(self):
        await self.before_load()
        for ext in self.extension_names():
            await self.load_extension(ext)
            log.info("loaded %s", ext)
        await self.before_sync()
        # コマンドは BOT が入っているすべてのサーバーに出る（使えるかは各 cog が見る）
        synced = await self.tree.sync()
        log.info("synced %d commands", len(synced))
        await self.after_sync()
        health.start(self)

    async def before_load(self) -> None:
        """機能を読み込む前にすること（BOT ごと）"""

    async def before_sync(self) -> None:
        """全体の sync の前にすること（BOT ごと。例: 以前のサーバー別の登録を消す）"""

    async def after_sync(self) -> None:
        """全体の sync の後にすること（BOT ごと。例: 運営用のサーバーにだけ別の sync）"""

    async def app_command_error(self, inter: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await on_app_command_error(inter, error)

    async def check_names(self) -> list[str]:
        """--check で出すコマンドの名前（BOT ごとに検証を足せる）"""
        return [c.name for c in self.tree.get_commands()]


def check(make_bot: Callable[[Path], BaseBot]) -> None:
    """起動前の検証（--check）。設定を読み、すべての機能を読み込んでコマンドを組み立てられるかを見る（Discord にはつながない）。
    make_bot(path) は、本物の DB（/data）には触らない一時的な DB で BOT を作る（表の作り方 MIGRATIONS も確かめる）"""
    async def run():
        bot = make_bot(Path(tempfile.mkdtemp()) / "check.db")
        for ext in bot.extension_names():
            await bot.load_extension(ext)
        names = await bot.check_names()
        print(f"ok: {len(bot.extension_names())} extensions, commands: {', '.join(names)}")
    asyncio.run(run())
