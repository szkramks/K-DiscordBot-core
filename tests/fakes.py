"""どの BOT の試験でも使える偽のオブジェクトと補助

BOT ごとに形が違うものは、それぞれの試験の中に置く。
"""
import asyncio
from types import SimpleNamespace

import discord


def arun(coro):
    return asyncio.run(coro)


def http_error(status: int = 403) -> discord.HTTPException:
    """Discord の API が断ったときの例外（403 なら Forbidden 相当の HTTPException）"""
    return discord.HTTPException(SimpleNamespace(status=status, reason="x"), "no")


class FakePerms:
    """permissions_for() が返す権限。指定しないものは True"""

    def __init__(self, **kw):
        for k in ("view_channel", "send_messages", "mention_everyone", "read_message_history", "connect",
                  "manage_events", "manage_guild", "administrator"):
            setattr(self, k, kw.get(k, True))


class FakeChannel:
    """テキストチャンネル。send した文と allowed_mentions を sent に残す。fail なら send が HTTPException"""

    def __init__(self, id: int = 55, guild=None, fail: bool = False, **perms):
        self.id, self.guild, self.fail = id, guild, fail
        self.perms = FakePerms(**perms)
        self.sent: list[tuple[str, discord.AllowedMentions | None]] = []

    def permissions_for(self, me):
        return self.perms

    async def send(self, text, allowed_mentions=None):
        if self.fail:
            raise http_error(500)
        self.sent.append((text, allowed_mentions))


class FakeGuild:
    """サーバー。channels（ID → チャンネル）と me（BOT のメンバー）を持つ"""

    def __init__(self, id: int = 1, **me_perms):
        self.id = id
        self.me = SimpleNamespace(guild_permissions=FakePerms(**me_perms), bot=True)
        self.channels: dict[int, object] = {}
        self.scheduled_events: list = []

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def add_channel(self, channel) -> "FakeChannel":
        channel.guild = self
        self.channels[channel.id] = channel
        return channel


class PanelInter:
    """設定パネルの Interaction。応答（edit_message・defer・send_message・followup）を log に残す。
    edit_message では、画面の footer の文と、部品ごとの押せる・押せないを記録する（保存中に押せなくなるかを見る）"""

    def __init__(self, log, guild_id: int = 1, user_id: int = 9, guild=None):
        self.log, self.guild_id, self.guild = log, guild_id, guild
        self.user = SimpleNamespace(id=user_id)
        self._done = False
        self.response = SimpleNamespace(is_done=lambda: self._done, edit_message=self._edit_message,
                                        send_message=self._send, defer=self._defer)
        self.followup = SimpleNamespace(send=self._followup)

    async def _edit_message(self, **kw):
        self._done = True
        # view は同じものを使い回すことがあるので、そのときの押せる・押せないを記録する
        self.log.append(("edit_message", kw["embed"].footer.text, [c.disabled for c in kw["view"].children]))

    async def _defer(self):
        self._done = True
        self.log.append(("defer",))

    async def _send(self, *a, **kw):
        self._done = True
        self.log.append(("send_message", a[0]))

    async def edit_original_response(self, **kw):
        self.log.append(("edit_original_response", kw["embed"].footer.text, [c.disabled for c in kw["view"].children]))

    async def _followup(self, text, **kw):
        self.log.append(("followup", text))
