"""core/posting.py: 投稿先の確認・足りない権限の文・@everyone の扱い"""
import logging

import discord
import pytest

from discordbot_core.posting import (EVERYONE_ONLY, failed_post_text, mention_or_warn, mentions, missing_perms,
                                     missing_post_permissions, missing_text, notice_content, post_target, try_send, writable)
from tests.fakes import FakeChannel, arun


class Perms:
    def __init__(self, **kw):
        for k in ("view_channel", "send_messages", "mention_everyone", "read_message_history", "connect"):
            setattr(self, k, kw.get(k, True))


class Channel:
    def __init__(self, guild, **perms):
        self.guild = guild
        self._perms = Perms(**perms)

    def permissions_for(self, me):
        return self._perms


class Guild:
    def __init__(self):
        self.id = 1
        self.me = object()
        self.channels = {}

    def get_channel(self, cid):
        return self.channels.get(cid)


log = logging.getLogger("test.posting")


def test_missing_perms_and_text():
    p = Perms(send_messages=False, mention_everyone=False)
    assert missing_perms(p, ("view_channel", "send_messages", "mention_everyone")) == [
        "メッセージを送信", "@everyone・@here・すべてのロールにメンション"]
    assert missing_text(["チャンネルを見る", "接続"]) == \
        "⚠️ BOT に足りない権限: チャンネルを見る・接続（サーバーかチャンネルの権限で、BOT のロールに許可してください）"


def test_missing_post_permissions_mention_only_when_needed():
    g = Guild()
    ch = Channel(g, mention_everyone=False)
    assert missing_post_permissions(ch, need_mention=False) == []
    assert missing_post_permissions(ch, need_mention=True) == ["@everyone・@here・すべてのロールにメンション"]


def test_writable():
    g = Guild()
    assert writable(Channel(g), g.me) is True
    assert writable(Channel(g, view_channel=False), g.me) is False
    assert writable(Channel(g, send_messages=False), g.me) is False


@pytest.mark.parametrize("channel_id,perms,expected,message", [
    (None, {}, None, "remind: channel not found (guild 1)"),
    (404, {}, None, "remind: channel not found (guild 1)"),
    (55, {"send_messages": False}, None, "remind: cannot post (guild 1, channel 55)"),
    (55, {}, "ok", None),
])
def test_post_target(caplog, channel_id, perms, expected, message):
    g = Guild()
    g.channels[55] = Channel(g, **perms)
    with caplog.at_level(logging.WARNING, logger="test.posting"):
        got = post_target(g, channel_id, log, "remind")
    assert (got is g.channels[55]) if expected else (got is None)
    assert [r.getMessage() for r in caplog.records] == ([message] if message else [])


def test_mention_or_warn(caplog):
    g = Guild()
    with caplog.at_level(logging.WARNING, logger="test.posting"):
        assert mention_or_warn(Channel(g), g, True, log, "auto") is True
        assert mention_or_warn(Channel(g, mention_everyone=False), g, False, log, "auto") is False
        assert mention_or_warn(Channel(g, mention_everyone=False), g, True, log, "auto") is False
    assert [r.getMessage() for r in caplog.records] == ["auto: no mention_everyone permission (guild 1)"]


def test_mentions():
    assert mentions(True) is EVERYONE_ONLY
    none = mentions(False)
    assert none.to_dict() == discord.AllowedMentions.none().to_dict() and none.everyone is False
    assert EVERYONE_ONLY.everyone is True and EVERYONE_ONLY.users is False and EVERYONE_ONLY.roles is False


def test_notice_content():
    assert notice_content("今日は固定") == "@everyone\n今日は固定"
    assert notice_content("  @everyone 今日は固定 ") == "@everyone 今日は固定"


def test_failed_post_text():
    assert failed_post_text(0) is None and failed_post_text(-1, "10/20（火）21:00") is None    # 0 件なら行を足さない
    assert failed_post_text(2) == "⚠️ 直近で出せなかった: 2 件"
    assert failed_post_text(1, None) == "⚠️ 直近で出せなかった: 1 件"
    assert failed_post_text(2, "10/20（火）21:00") == "⚠️ 直近で出せなかった: 2 件（最後 10/20（火）21:00）"


def test_try_send_returns_true_and_passes_text_and_mentions(caplog):
    ch = FakeChannel()
    with caplog.at_level(logging.WARNING, logger="test.posting"):
        assert arun(try_send(ch, "こんにちは", EVERYONE_ONLY, log, "remind", "guild 1")) is True
    assert ch.sent == [("こんにちは", EVERYONE_ONLY)] and caplog.records == []


def test_try_send_returns_false_and_logs_when_discord_refuses(caplog):
    ch = FakeChannel(fail=True)
    with caplog.at_level(logging.WARNING, logger="test.posting"):
        assert arun(try_send(ch, "こんにちは", mentions(False), log, "auto", "event 3")) is False
    assert [r.getMessage() for r in caplog.records] == ["auto: send failed (HTTPException, event 3)"]
    assert ch.sent == []


def test_try_send_does_not_hide_other_errors():
    class Broken:
        async def send(self, text, allowed_mentions=None):
            raise RuntimeError("bug")
    with pytest.raises(RuntimeError):
        arun(try_send(Broken(), "x", mentions(False), log, "auto", "event 3"))
