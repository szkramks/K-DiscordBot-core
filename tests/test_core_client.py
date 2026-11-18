"""core/client.py: 機能の自動発見・エラーの返し方。core は BOT の機能を import しない"""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from discordbot_core import client as core

CORE_DIR = Path(core.__file__).resolve().parent   # import した core の場所を見る


def test_core_does_not_import_features():
    """core は bot.features・bot.cogs・bot.ui や親（..）を import しない（どの BOT からも使えるように）"""
    bad = re.compile(r"^\s*(from \.\.|from bot\.|import bot\b)", re.M)
    for py in CORE_DIR.glob("*.py"):
        assert not bad.search(py.read_text()), py.name


def test_extensions_finds_packages_with_cog_and_plain_modules(tmp_path):
    pkg = tmp_path / "feats"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name in ("remind", "auto", "_draft"):
        (pkg / name).mkdir()
        (pkg / name / "__init__.py").write_text("")
        (pkg / name / "cog.py").write_text("")
    (pkg / "nocog").mkdir()
    (pkg / "nocog" / "__init__.py").write_text("")
    (pkg / "plain.py").write_text("")
    (pkg / "_hidden.py").write_text("")
    package = SimpleNamespace(__name__="feats", __path__=[str(pkg)])
    assert core.extensions(package) == ["feats.auto.cog", "feats.plain", "feats.remind.cog"]


def _inter(done=False):
    got = {}

    async def send_message(msg, **kw):
        got["msg"], got["ephemeral"] = msg, kw.get("ephemeral")

    async def followup(msg, **kw):
        got["followup"], got["ephemeral"] = msg, kw.get("ephemeral")
    return got, SimpleNamespace(response=SimpleNamespace(is_done=lambda: done, send_message=send_message),
                                followup=SimpleNamespace(send=followup),
                                command=SimpleNamespace(qualified_name="x"))


@pytest.mark.parametrize("error,msg", [
    (app_commands.CheckFailure(), core.NO_PERMISSION),
    (app_commands.CommandInvokeError(SimpleNamespace(name="x", qualified_name="x"),
                                     discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "no")), core.FORBIDDEN),
    (app_commands.AppCommandError("boom"), core.FAILED),
])
def test_app_command_error_messages(error, msg):
    got, inter = _inter()
    asyncio.run(core.on_app_command_error(inter, error))
    assert got["msg"] == msg and got["ephemeral"] is True


def test_app_command_error_uses_followup_when_responded():
    got, inter = _inter(done=True)
    asyncio.run(core.on_app_command_error(inter, app_commands.CheckFailure()))
    assert got["followup"] == core.NO_PERMISSION and "msg" not in got
