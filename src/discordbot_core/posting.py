"""投稿先（テキストチャンネル）に出すときの共通部分: 権限の確認・「足りない権限」の文・@everyone の扱い・送信を試す・出せなかった数の文

- BOT 全体の allowed_mentions は none のまま。@everyone を鳴らすメッセージだけ、送るときに mentions(True) を渡す。
- 画面の「足りない権限」（missing_post_permissions）と、見回りの「出せるか」（post_target / writable）は同じ判定を使う。
"""
import logging

import discord

PERM_LABELS = {
    "view_channel": "チャンネルを見る",
    "send_messages": "メッセージを送信",
    "read_message_history": "メッセージ履歴を読む",
    "mention_everyone": "@everyone・@here・すべてのロールにメンション",
    "connect": "接続",
}
MISSING_PERMISSIONS = "⚠️ BOT に足りない権限: {missing}（サーバーかチャンネルの権限で、BOT のロールに許可してください）"
EVERYONE = "@everyone"
EVERYONE_ONLY = discord.AllowedMentions(everyone=True, users=False, roles=False, replied_user=False)


def missing_perms(perms, attrs) -> list[str]:
    """perms（discord.Permissions）に attrs（PERM_LABELS のキー）のうち無いものの名前"""
    return [PERM_LABELS[a] for a in attrs if not getattr(perms, a)]


def missing_text(missing: list[str]) -> str:
    """「⚠️ BOT に足りない権限: …」の文（missing は missing_perms の結果）"""
    return MISSING_PERMISSIONS.format(missing="・".join(missing))


def missing_post_permissions(channel, need_mention: bool) -> list[str]:
    """BOT が投稿先に出すのに足りない権限（見る・送信。@everyone を付けるなら、そのメンションの権限も）"""
    attrs = ("view_channel", "send_messages") + (("mention_everyone",) if need_mention else ())
    return missing_perms(channel.permissions_for(channel.guild.me), attrs)


def writable(channel, me) -> bool:
    """me（guild.me）がそのチャンネルを見られて、メッセージを送れるか"""
    p = channel.permissions_for(me)
    return bool(p.view_channel and p.send_messages)


def post_target(guild: discord.Guild, channel_id: int | None, log: logging.Logger, label: str):
    """投稿先に書ける状態ならチャンネルを返す。書けなければ None（log に warning を 1 行）"""
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        log.warning("%s: channel not found (guild %s)", label, guild.id)
        return None
    if not writable(channel, guild.me):
        log.warning("%s: cannot post (guild %s, channel %s)", label, guild.id, channel_id)
        return None
    return channel


def mention_or_warn(channel, guild: discord.Guild, mention: bool, log: logging.Logger, label: str) -> bool:
    """@everyone を付けてよいか。権限がなければ付けない（鳴らない @everyone を出さない。log に warning を 1 行）"""
    if mention and not channel.permissions_for(guild.me).mention_everyone:
        log.warning("%s: no mention_everyone permission (guild %s)", label, guild.id)
        return False
    return mention


def mentions(mention: bool) -> discord.AllowedMentions:
    """send に渡す allowed_mentions。@everyone を付けるときだけ鳴らす"""
    return EVERYONE_ONLY if mention else discord.AllowedMentions.none()


async def try_send(channel, text: str, allowed_mentions: discord.AllowedMentions, log: logging.Logger, label: str,
                   ref: str) -> bool:
    """channel.send を試して、送れたら True。Discord が断ったとき（HTTPException）は log に warning を 1 行出して False。
    ref は log に添える識別子（"guild 1"・"event 3" など。呼ぶ側が決める）。HTTPException 以外の例外は拾わない"""
    try:
        await channel.send(text, allowed_mentions=allowed_mentions)
    except discord.HTTPException as e:
        log.warning("%s: send failed (%s, %s)", label, type(e).__name__, ref)
        return False
    return True


def failed_post_text(count: int, last: str | None = None) -> str | None:
    """画面に出す「⚠️ 直近で出せなかった: N 件（最後 …）」の 1 行。count が 0 以下なら None（行を足さない）。
    last は表示済みの日時の文字列（日時の表示は BOT ごとの timeparse にあるので、core は受け取るだけ）"""
    if count <= 0:
        return None
    return f"⚠️ 直近で出せなかった: {count} 件" + (f"（最後 {last}）" if last else "")


def notice_content(body: str) -> str:
    """先頭に @everyone を付ける（既にあれば付けない）"""
    body = body.strip()
    return body if body.startswith(EVERYONE) else f"{EVERYONE}\n{body}"
