"""設定パネル（本人にだけ見える画面）の土台

- Guarded: 開いた本人で、今も権限（既定は「サーバー管理」）を持つ人だけが押せる View。
- MainPanel: メインの画面。store・保存中の印（busy）・時間切れでボタンを消すための message_inter を持つ。
- SubView / PanelModal: 機能ごとのサブ画面と入力欄。「保存して読み直して ✅ を出す」を _save にまとめる。
- with_busy: 保存が終わるまで押せなくし、失敗したら DB の状態に戻して ⚠️ を出す。
"""
import logging

import discord

log = logging.getLogger(__name__)

TIMEOUT = 600        # パネルを操作できる秒数
PREVIEW = 300        # 画面に出す文の長さ（埋め込みの欄は 1,024 文字まで）
DENIED = "⚠️ この設定を変える権限がありません。"
SAVING = "⏳ 保存しています…"
SAVE_FAILED = "⚠️ 保存に失敗しました。もう一度試してください。"


def preview(text: str, limit: int = PREVIEW) -> str:
    """画面に出す文。長ければ切って「…」"""
    return text if len(text) <= limit else text[:limit] + "…"


async def allowed(inter: discord.Interaction, owner_id: int, permission: str = "manage_guild",
                  denied: str = DENIED) -> bool:
    """押した人が、開いた本人で、今も permission を持っているか。違えば本人にだけ知らせる"""
    ok = (inter.user.id == owner_id and isinstance(inter.user, discord.Member)
          and getattr(inter.user.guild_permissions, permission))
    if not ok:
        await inter.response.send_message(denied, ephemeral=True)
    return ok


async def edit_panel(inter: discord.Interaction, **kwargs) -> None:
    """パネルを描き直す。先に defer していれば、元のメッセージを直す（DB の処理が 3 秒を超えて Unknown interaction になるのを避ける）"""
    if inter.response.is_done():
        await inter.edit_original_response(**kwargs)
    else:
        await inter.response.edit_message(**kwargs)


async def with_busy(inter: discord.Interaction, panel: "MainPanel", render, work, reload=None) -> None:
    """保存が終わるまで、ボタン・プルダウンを押せなくする。
    1. すぐ、全部を押せなくした画面（「⏳ 保存しています…」）に直して返事をする（DB を待たずに返事をするので、3 秒を超えない）
    2. work() で保存する。work は、画面の下に出す文（✅ か ⚠️）を返す
    3. 保存が終わったら、その結果の画面に直し、押せる状態に戻す
    失敗したら reload() で DB の状態に戻し、⚠️ を出す。保存中に届いた操作（押せなくなる前に送られたもの）は、何もしない。
    render(note) は、今の状態の画面（embed と view の dict）を返す"""
    if panel.busy:
        await inter.response.defer()
        return
    panel.busy = True
    try:
        busy = render(SAVING)
        for item in getattr(busy.get("view"), "children", []):
            item.disabled = True
        await edit_panel(inter, **busy)
        try:
            note = await work()
        except Exception:
            log.exception("panel save failed")
            if reload:
                try:
                    await reload()
                except Exception:
                    log.exception("panel reload failed")
            note = SAVE_FAILED
        await edit_panel(inter, **render(note))
    finally:
        panel.busy = False


class Guarded(discord.ui.View):
    """ボタン・プルダウンは allowed のときだけ動かす"""
    PERMISSION = "manage_guild"

    def __init__(self, owner_id: int):
        super().__init__(timeout=TIMEOUT)
        self.owner_id = owner_id

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        return await allowed(inter, self.owner_id, self.PERMISSION)


class MainPanel(Guarded):
    """メインの画面。render(guild, note) をサブクラスが書く（embed と view の dict を返す）"""

    def __init__(self, store, owner_id: int):
        super().__init__(owner_id)
        self.store = store
        self.busy = False   # 保存している間は True（with_busy）。その間の操作は受けつけない
        self.message_inter: discord.Interaction | None = None  # 時間切れのときにボタンを消すため

    def render(self, guild: discord.Guild, note: str | None = None) -> dict:
        raise NotImplementedError

    async def show(self, inter: discord.Interaction, note: str | None = None) -> None:
        """パネルを今の設定で描き直す"""
        await edit_panel(inter, **self.render(inter.guild, note))

    async def on_timeout(self) -> None:
        if self.message_inter:
            try:
                await self.message_inter.edit_original_response(view=None)
            except discord.HTTPException:
                pass


class SubView(Guarded):
    """機能ごとのサブ画面。_render(note) と _reload(inter) をサブクラスが書く"""

    def __init__(self, panel: MainPanel):
        super().__init__(panel.owner_id)
        self.panel = panel

    def _render(self, note: str | None = None) -> dict:
        """この画面の今の状態（edit_panel に渡す embed と view）"""
        raise NotImplementedError

    async def _reload(self, inter: discord.Interaction) -> None:
        """この画面が見せる設定を、DB の今の状態に読み直す"""
        raise NotImplementedError

    async def _save(self, inter: discord.Interaction, save, note) -> None:
        """save() で保存して読み直し、note を出す（保存中は押せない）。note は文か、読み直したあとに呼ぶ関数（保存した値で文を作るとき）"""
        async def work():
            await save()
            await self._reload(inter)
            return note() if callable(note) else note
        await with_busy(inter, self.panel, self._render, work, lambda: self._reload(inter))


class PanelModal(discord.ui.Modal):
    """パネルから開く入力欄。送信のときもパネルと同じく権限を確かめ直す（開いてから送るまでに権限を外されたとき）"""

    def __init__(self, panel: MainPanel, **kwargs):
        super().__init__(timeout=TIMEOUT, **kwargs)
        self.panel = panel

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        return await allowed(inter, self.panel.owner_id, self.panel.PERMISSION)

    async def _save(self, inter: discord.Interaction, render, work, reload) -> None:
        """work() で保存し（返す文を画面の下に出す）、失敗したら reload() で戻す。render(note) はこの画面の embed と view"""
        await with_busy(inter, self.panel, render, work, reload)
