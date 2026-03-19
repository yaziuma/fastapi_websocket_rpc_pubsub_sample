# fastapi websocket RPC / PubSub sample

2 つの FastAPI ノードを立ち上げ、相互に WebSocket RPC と PubSub を接続して動きを確認するためのサンプルです。

- `server-a`: `http://127.0.0.1:60001`
- `server-b`: `http://127.0.0.1:60002`
- 各ノードは自分の WebSocket エンドポイントを公開します
- 登録 API を呼ぶと、相手ノードへ RPC / PubSub クライアントとして接続します
- WebSocket 接続時は `?token=...` による簡易認証を使います
- 操作は Swagger UI の `/docs` と可視化 UI の `/ui` から行えます

## これは何か

このプロジェクトは、以下の 2 つの通信パターンを 1 つのサンプルで比較しながら確認するためのものです。

- RPC: 特定の相手に対してメソッドを呼び出す 1 対 1 の通信
- PubSub: topic にイベントを publish し、購読中ノードへ配信する 1 対 N の通信

具体的には次の流れを試せます。

1. `server-a` と `server-b` を起動する
2. 片方向または相互登録で RPC / PubSub 接続を張る
3. RPC でジョブ登録や状態取得を行う
4. callback RPC でアラートを送る
5. PubSub でイベントを publish し、相手側の受信を確認する

## 構成

```text
.
├── config.toml
├── start_servers.sh
├── stop_servers.sh
└── src/rpc_pubsub_sample/
    ├── app_config.py
    ├── node_app.py
    ├── server_a.py
    ├── server_b.py
    └── templates/dashboard.html
```

主要ファイル:

- `src/rpc_pubsub_sample/node_app.py`
  - アプリ本体
  - 接続管理
  - RPC / PubSub エンドポイント
  - UI 用 API
- `src/rpc_pubsub_sample/server_a.py`
  - `server-a` のエントリポイント
- `src/rpc_pubsub_sample/server_b.py`
  - `server-b` のエントリポイント
- `src/rpc_pubsub_sample/templates/dashboard.html`
  - `/ui` で表示するダッシュボード
- `config.toml`
  - ポート、ノード名、認証トークンなどの設定

## 使用ライブラリ

このサンプルは、以下の 2 つのライブラリを中心に構築しています。

### `fastapi-websocket-rpc`

FastAPI 上で双方向 RPC を実現するライブラリです。サーバとクライアントの双方がメソッドを公開でき、`.other.method()` 形式で相手側のメソッドを呼べます。このサンプルでは、ノード情報取得、ジョブ登録、アラート通知などの RPC に使っています。

- GitHub: <https://github.com/permitio/fastapi_websocket_rpc>
- PyPI: <https://pypi.org/project/fastapi-websocket-rpc/>

### `fastapi-websocket-pubsub`

FastAPI 上で WebSocket ベースの PubSub を実現するライブラリです。topic 単位でイベントを publish / subscribe でき、このサンプルではノード間のイベント共有に使っています。

- GitHub: <https://github.com/permitio/fastapi_websocket_pubsub>
- PyPI: <https://pypi.org/project/fastapi-websocket-pubsub/>

### 謝意

このサンプルは `fastapi-websocket-rpc` と `fastapi-websocket-pubsub` の機能を組み合わせて成り立っています。メンテナとコントリビュータのみなさんに感謝します。

## セットアップ

前提:

- Python `>= 3.14`
- `uv`

依存関係をインストールします。

```bash
uv sync
```

## 設定

設定ファイルは `config.toml` です。

```toml
[fastapi]
reload = true

[server_a]
name = "server-a"
host = "127.0.0.1"
port = 60001
outbound_token = "token-server-a"

[server_a.accepted_tokens]
token-server-b = "server-b"

[server_b]
name = "server-b"
host = "127.0.0.1"
port = 60002
outbound_token = "token-server-b"

[server_b.accepted_tokens]
token-server-a = "server-a"
```

ポイント:

- `outbound_token`: 自ノードが相手へ接続するときに使うトークン
- `accepted_tokens`: 相手から接続されるときに受け入れるトークン一覧
- `reload = true`: `start_servers.sh` で `uvicorn --reload` を有効化

## 起動

まとめて起動:

```bash
./start_servers.sh
```

停止:

```bash
./stop_servers.sh
```

`start_servers.sh` は 2 つの uvicorn を独立したプロセスグループで起動し、`stop_servers.sh` はそれらをまとめて停止します。

ログ出力先:

- `logs/server-a.log`
- `logs/server-b.log`

個別起動する場合:

```bash
PYTHONPATH=src uv run python -m uvicorn rpc_pubsub_sample.server_a:app --host 127.0.0.1 --port 60001
```

```bash
PYTHONPATH=src uv run python -m uvicorn rpc_pubsub_sample.server_b:app --host 127.0.0.1 --port 60002
```

## 使い方

### 1. 画面を開く

- `http://127.0.0.1:60001/docs`
- `http://127.0.0.1:60002/docs`
- `http://127.0.0.1:60001/ui`
- `http://127.0.0.1:60002/ui`

### 2. 接続を開始する

片方向登録:

`server-a` から `server-b` へ接続する例:

```json
{
  "target": "all",
  "remote_name": "server-b",
  "rpc_url": "ws://127.0.0.1:60002/ws/rpc",
  "pubsub_url": "ws://127.0.0.1:60002/ws/pubsub"
}
```

相互登録:

```json
{
  "target": "all",
  "remote_name": "server-b",
  "remote_base_url": "http://127.0.0.1:60002"
}
```

相互登録を `server-a` 側で実行すると、`server-a -> server-b` と `server-b -> server-a` の両方をまとめて登録します。

### 3. 状態を確認する

`GET /health` で次を確認できます。

- `rpc_registration_enabled`
- `pubsub_registration_enabled`
- `rpc_connected`
- `pubsub_connected`
- `incoming_rpc_channels`
- `recent_pubsub_events`

### 4. RPC を試す

相手サーバ側に対して呼ぶ RPC:

- `POST /rpc/server/get-node-info`
- `POST /rpc/server/submit-job`
- `POST /rpc/server/list-jobs`

`submit-job` の例:

```json
{
  "job_type": "配送手配",
  "priority": "high",
  "parameters": {
    "order_id": "9999",
    "warehouse": "tokyo-1",
    "force": true
  }
}
```

相手 callback 側に対して呼ぶ RPC:

- `POST /rpc/client/push-alert`
- `POST /rpc/client/list-alerts`
- `POST /rpc/client/status`

`push-alert` の例:

```json
{
  "severity": "warning",
  "message": "注文9999の在庫が足りません。至急確認してください。"
}
```

### 5. PubSub を試す

`POST /pubsub/publish` の例:

```json
{
  "topic": "order.status",
  "event_type": "updated",
  "payload": {
    "order_id": "9999",
    "status": "決済完了",
    "customer_id": "C-1201"
  }
}
```

受信結果は `GET /pubsub/events` または `/ui` で確認できます。

### 6. 接続を解除する

片方向解除:

```json
{
  "target": "all"
}
```

相互解除:

```json
{
  "target": "all",
  "remote_base_url": "http://127.0.0.1:60002"
}
```

## `/ui` で見られるもの

`/ui` は API テスターではなく、通信の流れを掴むためのダッシュボードです。

- Local / Remote ノードの状態
- RPC パイプの開通状況
- PubSub パイプの開通状況
- Job 一覧
- Alert 一覧
- PubSub イベントのタイムライン
- 直近レスポンスの要約と JSON 詳細

## 主なエンドポイント

### Meta / UI

- `GET /`
- `GET /health`
- `GET /ui`
- `GET /ui/state`
- `POST /ui/remote-snapshot`

### Registration

- `POST /registration/connect`
- `POST /registration/connect-mutual`
- `POST /registration/disconnect`
- `POST /registration/disconnect-mutual`

### RPC

- `GET /rpc/methods`
- `POST /rpc/server/get-node-info`
- `POST /rpc/server/submit-job`
- `POST /rpc/server/list-jobs`
- `POST /rpc/client/push-alert`
- `POST /rpc/client/list-alerts`
- `POST /rpc/client/status`

### PubSub

- `POST /pubsub/publish`
- `GET /pubsub/events`

### WebSocket

- `GET /ws/rpc?token=...`
- `GET /ws/pubsub?token=...`

## 開発メモ

- 起動しただけでは peer への接続は始まりません
- 接続先を変更したい場合は、先に切断してください
- 自分自身の `/ws/rpc` や `/ws/pubsub` を接続先に指定すると `422` を返します
- 同じ登録内容を再実行すると `409 Conflict` を返します

## ライセンス

このリポジトリのライセンスが必要なら、別途 `LICENSE` を追加してください。
