# fastapi websocket RPC / PubSub sample

`tmp.md` の内容をベースに、FastAPI を 2 つ立てて相互接続するサンプルです。Python ファイルは `src/rpc_pubsub_sample/` 配下にあります。操作は FastAPI の `/docs` または `/ui` から行えます。

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

簡易 UI:

- `http://127.0.0.1:60001/ui`
- `http://127.0.0.1:60002/ui`

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

### 3. 相手サーバの RPC 呼び出し

相手サーバ側では次の method を公開しています。

- `get_node_info`: ノード名、接続状態、保存件数を取得
- `submit_job`: 相手サーバへジョブを登録
- `list_jobs`: 相手サーバに登録されたジョブ一覧を取得

### 4. 相手コールバックの RPC 呼び出し

相手 callback 側では次の method を公開しています。

- `push_alert`: 相手 callback 側へアラートを追加
- `list_alerts`: 相手 callback 側に届いたアラート一覧を取得
- `client_status`: 相手 callback 側の詳細状態を取得

公開中の sample method 一覧は `GET /rpc/methods` で確認できます。`/ui` ではこの一覧をもとに、公開種別と method を選んで対応する個別エンドポイントを呼びます。

### 5. PubSub 配信

`POST /pubsub/publish` で次のように送信します。

```bash
{"topic":"events","message":"event from server-a"}
```

その後、peer 側の `/docs` で `GET /pubsub/events` を実行して受信結果を確認します。

### 6. 登録解除

`POST /registration/disconnect` を `{"target":"all"}` で実行すると、登録ループを止められます。

片側操作だけで双方を解除したい場合は、`POST /registration/disconnect-mutual` を使います。

```bash
{
  "target": "all",
  "remote_base_url": "http://127.0.0.1:60002"
}
```

## `/ui` について

`/ui` は 1 画面で以下を行うための簡易 Web UI です。

- 単方向登録
- 相互登録
- 単方向解除
- 相互解除
- RPC 公開種別と method の選択
- 選択した method に対応する個別エンドポイントの実行
- PubSub publish
- ローカル状態確認
- リモート状態確認

## `curl` で試す場合

`/docs` を使わずに確認したい場合は、README の各 JSON 例をそのまま `curl` で送っても構いません。

## エンドポイント

- `GET /health`: RPC / PubSub の接続状態と最近の受信イベント
- `POST /registration/connect`: `rpc` / `pubsub` / `all` の登録開始
  接続先はリクエストボディで指定する
- `POST /registration/connect-mutual`: 相手ノードと相互登録する
- `POST /registration/disconnect`: `rpc` / `pubsub` / `all` の登録解除
- `POST /registration/disconnect-mutual`: 相手ノードと相互解除する
- `GET /ui`: 1画面で操作できる簡易 UI
- `POST /ui/remote-snapshot`: UI 用に相手ノードの状態を取得する
- `GET /rpc/methods`: このサンプルで公開している RPC method 一覧を返す
- `POST /rpc/server/get-node-info`: peer 側の `get_node_info` を呼ぶ
- `POST /rpc/server/submit-job`: peer 側の `submit_job` を呼ぶ
- `POST /rpc/server/list-jobs`: peer 側の `list_jobs` を呼ぶ
- `POST /rpc/client/push-alert`: peer 側の `push_alert` を呼ぶ
- `POST /rpc/client/list-alerts`: peer 側の `list_alerts` を呼ぶ
- `POST /rpc/client/status`: peer 側の `client_status` を呼ぶ
- `POST /pubsub/publish`: 自サーバの PubSub endpoint から publish する
- `GET /pubsub/events`: 受信した PubSub イベント一覧
