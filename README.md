# fastapi websocket RPC / PubSub sample

`tmp.md` の内容をベースに、FastAPI を 2 つ立てて相互接続するサンプルです。Python ファイルは `src/rpc_pubsub_sample/` 配下にあります。操作は FastAPI の `/docs` から行う前提です。

- `src/rpc_pubsub_sample/server_a.py`: `127.0.0.1:60001`
- `src/rpc_pubsub_sample/server_b.py`: `127.0.0.1:60002`
- 各アプリは自分の WebSocket サーバを公開し、登録 API を叩くとユーザが指定した接続先へ RPC / PubSub クライアントとして接続します
- WebSocket 接続時は `?token=...` で簡易認証します

## セットアップ

```bash
uv sync
```

## 設定

ポート番号や host は [`config.toml`](/home/yuichi/projects/fastapi_websocket_rpc_pubsub_sample/config.toml) で指定します。

```toml
[server_a]
host = "127.0.0.1"
port = 60001

[server_b]
host = "127.0.0.1"
port = 60002
```

`src/rpc_pubsub_sample/server_a.py` / `src/rpc_pubsub_sample/server_b.py` と `start_servers.sh` は同じ設定ファイルを参照します。

## 起動

まとめて起動:

```bash
./start_servers.sh
```

停止:

```bash
./stop_servers.sh
```

`start_servers.sh` は各サーバを独立したプロセスグループで起動し、`stop_servers.sh` はそのグループ単位で停止します。
ログは Python の `logging` で [`logs/server-a.log`](/home/yuichi/projects/fastapi_websocket_rpc_pubsub_sample/logs/server-a.log) と [`logs/server-b.log`](/home/yuichi/projects/fastapi_websocket_rpc_pubsub_sample/logs/server-b.log) に出力されます。`uvicorn` のログも同じファイルに追記されます。

個別に起動する場合:

ターミナル 1:

```bash
PYTHONPATH=src uv run python -m uvicorn rpc_pubsub_sample.server_a:app --host 127.0.0.1 --port 60001
```

ターミナル 2:

```bash
PYTHONPATH=src uv run python -m uvicorn rpc_pubsub_sample.server_b:app --host 127.0.0.1 --port 60002
```

起動しただけでは peer への登録は行いません。ユーザが登録 API を叩くと、RPC / PubSub の接続を開始します。

## `/docs` からの操作手順

両サーバ起動後、以下をブラウザで開いて操作します。

- `http://127.0.0.1:60001/docs`
- `http://127.0.0.1:60002/docs`

`config.toml` のポートを変更している場合は、その値に読み替えてください。

### 1. 登録開始

まず両サーバの `/docs` で `POST /registration/connect` を開き、接続先を指定して実行します。

```bash
{
  "target": "all",
  "remote_name": "server-b",
  "rpc_url": "ws://127.0.0.1:60002/ws/rpc",
  "pubsub_url": "ws://127.0.0.1:60002/ws/pubsub"
}
```

逆方向は `remote_name` と URL を入れ替えて実行します。認証トークンは各サーバの `config.toml` にある `outbound_token` を自動利用します。必要に応じて `rpc` または `pubsub` だけ登録することもできます。

同じ内容の登録を実行中に再度 `POST /registration/connect` すると `409 Conflict` を返します。接続先を変えたい場合も、先に `POST /registration/disconnect` を実行してください。自分自身の `/ws/rpc` や `/ws/pubsub` を接続先に指定した場合は `422` を返します。

片側操作だけで双方を登録したい場合は、`POST /registration/connect-mutual` を使います。

```bash
{
  "target": "all",
  "remote_name": "server-b",
  "remote_base_url": "http://127.0.0.1:60002"
}
```

これを `server-a` 側の `/docs` から実行すると、`server-a -> server-b` の登録と、`server-b -> server-a` の登録を続けて行います。

### 2. 状態確認

`GET /health` を実行し、`rpc_registration_enabled` / `pubsub_registration_enabled` と
`rpc_connected` / `pubsub_connected` が `true` になっていることを確認します。

### 3. RPC 呼び出し

`POST /rpc/call-peer` を開き、たとえば次を送ります。

```bash
{"message":"hello from server-a"}
```

### 4. 双方向 RPC 呼び出し

`POST /rpc/call-peer-client` を実行すると、peer 側が client として公開している method を呼べます。

```bash
{"message":"callback please"}
```

### 5. PubSub 配信

`POST /pubsub/publish` で次のように送信します。

```bash
{"topic":"events","message":"event from server-a"}
```

その後、peer 側の `/docs` で `GET /pubsub/events` を実行して受信結果を確認します。

### 6. 登録解除

`POST /registration/disconnect` を `{"target":"all"}` で実行すると、登録ループを止められます。

## `curl` で試す場合

`/docs` を使わずに確認したい場合は、README の各 JSON 例をそのまま `curl` で送っても構いません。

## エンドポイント

- `GET /health`: RPC / PubSub の接続状態と最近の受信イベント
- `POST /registration/connect`: `rpc` / `pubsub` / `all` の登録開始
  接続先はリクエストボディで指定する
- `POST /registration/connect-mutual`: 相手ノードと相互登録する
- `POST /registration/disconnect`: `rpc` / `pubsub` / `all` の登録解除
- `POST /rpc/call-peer`: peer サーバの RPC メソッドを呼ぶ
- `POST /rpc/call-peer-client`: peer 側 FastAPI が公開している RPC client method を呼ぶ
- `POST /rpc/client-status`: peer 側 FastAPI の RPC client 状態を取得する
- `POST /pubsub/publish`: 自サーバの PubSub endpoint から publish する
- `GET /pubsub/events`: 受信した PubSub イベント一覧
