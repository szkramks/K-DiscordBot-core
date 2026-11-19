# discordbot-core

> **お断り**
>
> - これは作者（GitHub の `szkramks`）が、自分で作る Discord BOT を維持・管理するために作った部品です。ほかの人が使うことは考えていません。
> - 作者の BOT の都合で、予告なく変えたり、呼び方の違う版を出したりすることがあります。
> - 質問・不具合の報告・Issue・Pull Request には応えないことがあります。動作の保証はありません。使う場合は自分の責任でお願いします。
> - 公開しているのは、作者の BOT がイメージを作るときに、鍵なしで取り込めるようにするためです。

## 何か

[discord.py](https://discordpy.readthedocs.io/) で作る BOT の共通の土台です。起動・設定の読み方・SQLite・ヘルスチェック・見回り（定期の処理）・投稿先の確認・設定パネルの土台をまとめてあります。BOT の機能そのものは含みません。

## 動作環境

**Linux で、Docker を使います。** ほかの OS や、Docker を使わない動かし方には対応しません。

- Linux
- Docker と Docker Compose（入れ方は Docker の公式の手順に従ってください）
- イメージの中の Python は 3.12
- Discord の Developer Portal で BOT を作り、トークンを用意しておく

## はじめ方

まっさらな Linux で、BOT を 1 つ動かすまでの最小の形です。

### フォルダー構成

```
mybot/
  Dockerfile
  docker-compose.yml
  requirements.txt
  requirements-core.txt
  .env
  bot/
    __init__.py            # 空
    __main__.py
    client.py
    features/
      __init__.py          # 空
      hello/
        __init__.py        # 空
        cog.py
```

### requirements.txt

```
discord.py>=2.4,<3
python-dotenv>=1.0,<2
```

### requirements-core.txt

使う版の [Release](https://github.com/szkramks/K-DiscordBot-core/releases) の本文にある 1 行を、そのまま貼ります（ハッシュ付き）。

```
discordbot-core @ https://github.com/szkramks/K-DiscordBot-core/releases/download/v1.4.0/discordbot_core-1.4.0-py3-none-any.whl --hash=sha256:<Release の本文のハッシュ>
```

`requirements.txt` と分けているのは、ハッシュ付きの行を同じファイルに入れると、ほかの行にもハッシュが要るためです。

### Dockerfile

```dockerfile
FROM python:3.12-alpine
# タイムゾーンの情報（tzdata）と、DB を置く /data を用意する。root では動かさない
RUN apk add --no-cache tzdata && \
    addgroup -g 10001 bot && adduser -D -H -u 10001 -G bot bot && \
    mkdir /data && chown bot:bot /data
ENV TZ=Asia/Tokyo PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt requirements-core.txt ./
# wheel だけを入れる。core は URL とハッシュで固定する
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt && \
    pip install --no-cache-dir --only-binary=:all: --no-deps --require-hashes -r requirements-core.txt
COPY bot/ ./bot/
USER bot
# BOT が Discord につながっている間、1 分ごとに /tmp/alive が書かれる。3 分より古ければ unhealthy
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
  CMD python -c "import os,sys,time; sys.exit(0 if time.time() - os.path.getmtime('/tmp/alive') < 180 else 1)"
CMD ["python", "-m", "bot"]
```

core が前提にしていること:

- **タイムゾーン**: 時刻は JST で持ちます。タイムゾーンの情報を入れ（alpine なら `apk add tzdata`）、`TZ=Asia/Tokyo` にします。
- **`/data`**: DB（`/data/bot.db`）を置きます。compose で名前付きボリュームにします。
- **`/tmp/alive`**: ヘルスチェックの合図です。BOT が Discord につながっていて、見回りが遅れていない間だけ、1 分ごとに書かれます。

### docker-compose.yml

```yaml
services:
  bot:
    build: .
    env_file: .env
    read_only: true           # 書くのは DB（/data）と合図の /tmp/alive だけ
    tmpfs:
      - /tmp:size=1m
    volumes:
      - bot_data:/data
    restart: always           # 見回りが長く止まると BOT は自分で終了し、作り直される

volumes:
  bot_data:
```

### .env

```
DISCORD_TOKEN=ここにトークン
```

`.env` は Git に入れません。

### bot/\_\_main\_\_.py

```python
"""起動: python -m bot"""
import logging

from discordbot_core.config import load_env, require

from .client import MyBot

if __name__ == "__main__":
    load_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S%z",
    )
    # log_handler=None: discord.py に別の出力先を付けさせない（付けるとログが 2 回ずつ出る）
    MyBot().run(require("DISCORD_TOKEN"), log_handler=None)
```

### bot/client.py

```python
"""BOT 本体。共通の土台に、この BOT の機能と DB を渡す"""
from discordbot_core.client import BaseBot
from discordbot_core.store import BaseStore

from . import features


class Store(BaseStore):
    # 表を足す・変えるときは、末尾に CREATE TABLE / ALTER TABLE を足す（並びは変えない）
    MIGRATIONS: list[str] = []


class MyBot(BaseBot):
    def __init__(self, store: Store | None = None):
        super().__init__(features=features, store=store or Store())
```

### bot/features/hello/cog.py

`bot/features/` の下の、`cog.py` を持つフォルダーが起動のときにすべて読み込まれます（`_` で始まるフォルダーは読み込みません）。

```python
"""/hello: BOT が動いているかを確かめる"""
import discord
from discord import app_commands
from discord.ext import commands


class Hello(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="hello", description="BOT が動いているか確かめます")
    @app_commands.guild_only()
    async def hello(self, inter: discord.Interaction):
        await inter.response.send_message("ok", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hello(bot))
```

### 起動

```bash
docker compose up -d --build
```

```bash
docker compose logs -f
```

## 中身

import は `from discordbot_core.client import BaseBot` の形です。時刻は JST で持ちます。

| ファイル | 何をするか | 使う側で用意するもの |
|---|---|---|
| `client.py` | `BaseBot`: 機能の自動読み込み（`features/<機能>/cog.py`）・コマンドの sync・ヘルスチェックの開始・エラーの返し方（権限なし／BOT の権限不足／それ以外）・`check()`（Discord につながずに、機能を読み込めるかを確かめる）・`exit_code`（0 以外で `close()` すると `run()` のあとその番号で終了する） | `BaseBot` を継承し `features`・`store`（必要なら `intents`・`allowed_installs`・`allowed_contexts`・`tree_cls`）を渡す。sync の前後の処理は `before_load`／`before_sync`／`after_sync` を上書きする |
| `config.py` | `.env` の読み方: `load_env()`・`require(NAME)`（なければ `SystemExit`）・`optional(NAME, default)` | 項目の名前 |
| `store.py` | `BaseStore`: 接続（別の接続の書き込みを `BUSY_TIMEOUT` = 30 秒待つ）・`MIGRATIONS` を当てる（`PRAGMA user_version`）・`@threaded`（同期の本体を async で呼ぶ）・`DT_FMT`・`JST`・`NOW_SQL`・`parse_dt`／`fmt_dt`（JST）・`DB_PATH` | `MIGRATIONS` の並びと、機能ごとの読み書き（`@threaded` を付けた同期の本体） |
| `health.py` | Discord につながっていて、見回りがどれも遅れていない間、1 分ごとに `/tmp/alive` を書く。見回りごとの「最後に最後まで動けた時刻」をメモリーだけに持ち（`WATCHES`）、間隔 × 5 を過ぎたら合図を止めて `watch stale` を WARNING、10 分か間隔 × 6 の長いほうを過ぎたら `watch stale too long, exiting for restart` を ERROR で残して BOT を閉じ、0 以外で終了する（Docker の `restart: always` が作り直す） | なし（`guarded` を付けた見回りを `start_once` で始めれば自動で見張られる） |
| `backup.py` | `dump(db_path)`: DB を SQL の文で書き出す（`user_version` も） | `dump(DB_PATH)` を呼ぶモジュール |
| `tasks.py` | 見回りの形: `start_once(loop)`（`guarded` の見回りは始めるときに `health.WATCHES` へ名前と間隔を登録する）・`for_each_guild(bot, ids, work, log, label)`（サーバーごとに例外を捕まえ `<label> failed`）・`guarded(log, label)`（見回りの本体を包み、例外で止めず `<label> watch failed` を残して次の回に進む。最後まで動けた回だけ `health.WATCHES` に記録する。`@tasks.loop` の下に付ける）・`Daily`（1 日 1 回。`due(now)`、古い記録の掃除は `purge_once(now, purge, log, label)`） | cog の `on_ready`／`tasks.loop` から呼ぶ |
| `posting.py` | 投稿先の確認: `post_target`（見る・送信がなければ None とログ）・`writable`・`missing_post_permissions`／`missing_perms`／`missing_text`（「足りない権限」の文）・`mention_or_warn`／`mentions`／`EVERYONE_ONLY`（@everyone）・`notice_content`・`try_send`（送信を試して成否を返す。断られたら log に 1 行）・`failed_post_text`（「⚠️ 直近で出せなかった: N 件（最後 …）」の 1 行。日時は表示済みの文字列で受け取る） | なし |
| `panel.py` | 設定パネルの土台: `Guarded`（開いた本人で権限を持つ人だけ）・`MainPanel`・`SubView`（`_render`／`_reload`／`_save`）・`PanelModal`・`edit_panel`・`with_busy`（保存中は押せなくする）・`preview` | 画面ごとの `View`／`Modal` |

## 版

版は `discordbot_core.CORE_VERSION` です。付け方:

- 呼び方を変える → 大きい版（1.x.x → 2.0.0）
- 追加だけ → 真ん中（1.4.x → 1.5.0）
- 直しだけ → 最後（1.4.0 → 1.4.1）

使う版は `requirements-core.txt` の 1 行で決まります。上げるときは、その行を新しい Release の本文の 1 行に替えます。

| 版 | 変わった点 |
|---|---|
| 1.0.0 | 最初の版 |
| 1.1.0 | 追加: `posting.try_send`・`posting.failed_post_text`・`tasks.Daily.purge_once` |
| 1.2.0 | `tasks.guarded` を追加（見回りの本体の例外で `tasks.loop` を止めない）。DB の待ち時間の既定を 30 秒に |
| 1.3.0 | 見回りの生死の見張りを追加（`health.Watches`・`WATCHES`・`tick`）。見回りが遅れると `/tmp/alive` を止め、長く止まると BOT が自分で終了する。`BaseBot.exit_code`・`BaseBot.run` を追加 |
| 1.4.0 | 時刻を JST で持つように変更（`parse_dt`・`fmt_dt` は JST。`JST`・`NOW_SQL` を追加）。古い版で書いた DB の時刻は使う側で直す必要がある。wheel で配る最初の版 |
| 1.4.1 | `BaseBot` に `Intents.none()` を渡しても既定のインテントに置き換わらないように直した |
