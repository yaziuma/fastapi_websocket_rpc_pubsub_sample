from __future__ import annotations

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from typing import Literal
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, HTTPException, Query, Request as FastAPIRequest, WebSocket, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi_websocket_pubsub import PubSubClient, PubSubEndpoint
from fastapi_websocket_rpc import RpcMethodsBase, WebSocketRpcClient, WebsocketRPCEndpoint
from fastapi_websocket_rpc.rpc_channel import RpcChannel
from pydantic import BaseModel, Field


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def configure_file_logging(node_name: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{node_name}.log"
    handler_key = f"rpc-pubsub-file-{node_name}"

    formatter = logging.Formatter(LOG_FORMAT)
    targets = [
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("fastapi_ws_rpc"),
    ]

    for logger in targets:
        if any(getattr(handler, "_rpc_pubsub_key", None) == handler_key for handler in logger.handlers):
            continue
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler._rpc_pubsub_key = handler_key
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.setLevel(logging.INFO)

    logging.getLogger("uvicorn").propagate = True
    logging.getLogger("uvicorn.error").propagate = True
    logging.getLogger("uvicorn.access").propagate = True
    logging.getLogger("fastapi_ws_rpc").propagate = True

    return log_path


@dataclass(frozen=True)
class NodeConfig:
    name: str
    host: str
    port: int
    outbound_token: str
    accepted_tokens: dict[str, str]
    pubsub_topic: str = "events"


class RpcCallRequest(BaseModel):
    message: str = Field(
        ...,
        description="peer に送るメッセージ",
        examples=["hello from server-a"],
    )


class PubSubPublishRequest(BaseModel):
    message: str = Field(
        ...,
        description="publish するメッセージ",
        examples=["event from server-a"],
    )
    topic: str = Field(
        default="events",
        description="publish 先のトピック名",
        examples=["events"],
    )


class RegistrationRequest(BaseModel):
    target: Literal["rpc", "pubsub", "all"] = Field(
        default="all",
        description="登録対象。`rpc` / `pubsub` / `all` を指定",
        examples=["all"],
    )
    remote_name: str | None = Field(
        default=None,
        description="接続先を識別するための任意の名前。RPC client method 呼び出し時の相手識別にも使います。",
        examples=["server-b"],
    )
    rpc_url: str | None = Field(
        default=None,
        description="接続先の RPC WebSocket URL。`target` が `rpc` または `all` のとき必須です。",
        examples=["ws://127.0.0.1:60002/ws/rpc"],
    )
    pubsub_url: str | None = Field(
        default=None,
        description="接続先の PubSub WebSocket URL。`target` が `pubsub` または `all` のとき必須です。",
        examples=["ws://127.0.0.1:60002/ws/pubsub"],
    )


class MutualRegistrationRequest(BaseModel):
    target: Literal["rpc", "pubsub", "all"] = Field(
        default="all",
        description="相互登録の対象。`rpc` / `pubsub` / `all` を指定",
        examples=["all"],
    )
    remote_name: str = Field(
        ...,
        description="相手ノードの識別名",
        examples=["server-b"],
    )
    remote_base_url: str = Field(
        ...,
        description="相手ノードの HTTP ベース URL。例: `http://127.0.0.1:60002`",
        examples=["http://127.0.0.1:60002"],
    )


class MutualDisconnectRequest(BaseModel):
    target: Literal["rpc", "pubsub", "all"] = Field(
        default="all",
        description="相互解除の対象。`rpc` / `pubsub` / `all` を指定",
        examples=["all"],
    )
    remote_base_url: str = Field(
        ...,
        description="相手ノードの HTTP ベース URL。例: `http://127.0.0.1:60002`",
        examples=["http://127.0.0.1:60002"],
    )


class RemoteSnapshotRequest(BaseModel):
    remote_base_url: str = Field(
        ...,
        description="状態取得対象ノードの HTTP ベース URL",
        examples=["http://127.0.0.1:60002"],
    )


@dataclass
class ConnectionTarget:
    remote_name: str
    rpc_url: str | None = None
    pubsub_url: str | None = None


class NodeRuntime:
    def __init__(self, config: NodeConfig):
        self.config = config
        self.logger = logging.getLogger(config.name)
        self.shutdown_event = asyncio.Event()
        self.rpc_client: WebSocketRpcClient | None = None
        self.pubsub_client: PubSubClient | None = None
        self.rpc_task: asyncio.Task[Any] | None = None
        self.pubsub_task: asyncio.Task[Any] | None = None
        self.rpc_registration_enabled = False
        self.pubsub_registration_enabled = False
        self.connection_target: ConnectionTarget | None = None
        self.incoming_rpc_channels: dict[str, RpcChannel] = {}
        self.received_pubsub_events: deque[dict[str, Any]] = deque(maxlen=20)
        self.jobs: deque[dict[str, Any]] = deque(maxlen=50)
        self.alerts: deque[dict[str, Any]] = deque(maxlen=50)
        self.rpc_endpoint = WebsocketRPCEndpoint(
            NodeRpcServerMethods(self),
            on_connect=[self.on_rpc_connect],
            on_disconnect=[self.on_rpc_disconnect],
        )
        self.pubsub_endpoint = PubSubEndpoint()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.shutdown_event.set()
        await self.disable_registrations("all")

    @property
    def local_base_url(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"

    @property
    def local_rpc_ws_url(self) -> str:
        return f"ws://{self.config.host}:{self.config.port}/ws/rpc"

    @property
    def local_pubsub_ws_url(self) -> str:
        return f"ws://{self.config.host}:{self.config.port}/ws/pubsub"

    def base_url_to_ws_url(self, base_url: str, path: str) -> str:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise HTTPException(status_code=422, detail="remote_base_url must start with http:// or https://")
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((ws_scheme, parsed.netloc, path, "", "", ""))

    async def request_remote_json(
        self,
        remote_base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_url = urljoin(remote_base_url.rstrip("/") + "/", path.lstrip("/"))

        def _request() -> dict[str, Any]:
            body = None if payload is None else json.dumps(payload).encode()
            request = UrlRequest(
                target_url,
                data=body,
                headers={"content-type": "application/json", "accept": "application/json"},
                method=method,
            )
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode())

        try:
            return await asyncio.to_thread(_request)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"failed to call remote endpoint {target_url}: {exc}",
            ) from exc

    def _is_same_target(
        self,
        current: ConnectionTarget | None,
        remote_name: str,
        rpc_url: str | None,
        pubsub_url: str | None,
    ) -> bool:
        return (
            current is not None
            and current.remote_name == remote_name
            and current.rpc_url == rpc_url
            and current.pubsub_url == pubsub_url
        )

    def _is_self_ws_url(self, raw_url: str, expected_path: str) -> bool:
        parsed = urlparse(raw_url)
        default_port = 443 if parsed.scheme == "wss" else 80
        port = parsed.port or default_port
        return (
            parsed.hostname in {"127.0.0.1", "localhost", self.config.host}
            and port == self.config.port
            and parsed.path == expected_path
        )

    def set_connection_target(self, request: RegistrationRequest) -> ConnectionTarget:
        existing = self.connection_target
        remote_name = request.remote_name or (existing.remote_name if existing else None)
        rpc_url = request.rpc_url or (existing.rpc_url if existing else None)
        pubsub_url = request.pubsub_url or (existing.pubsub_url if existing else None)

        if remote_name is None:
            raise HTTPException(status_code=422, detail="remote_name is required")
        if request.target in ("rpc", "all") and rpc_url is None:
            raise HTTPException(status_code=422, detail="rpc_url is required")
        if request.target in ("pubsub", "all") and pubsub_url is None:
            raise HTTPException(status_code=422, detail="pubsub_url is required")
        if request.target in ("rpc", "all") and rpc_url is not None and self._is_self_ws_url(rpc_url, "/ws/rpc"):
            raise HTTPException(status_code=422, detail="self rpc_url is not allowed")
        if request.target in ("pubsub", "all") and pubsub_url is not None and self._is_self_ws_url(pubsub_url, "/ws/pubsub"):
            raise HTTPException(status_code=422, detail="self pubsub_url is not allowed")

        if self._is_same_target(existing, remote_name, rpc_url, pubsub_url):
            if (
                (request.target in ("rpc", "all") and self.rpc_registration_enabled)
                or (request.target in ("pubsub", "all") and self.pubsub_registration_enabled)
            ):
                raise HTTPException(
                    status_code=409,
                    detail="the same registration is already active; disconnect first",
                )
        elif existing is not None and (self.rpc_registration_enabled or self.pubsub_registration_enabled):
            raise HTTPException(
                status_code=409,
                detail="registration target is already active; disconnect before changing target",
            )

        self.connection_target = ConnectionTarget(
            remote_name=remote_name,
            rpc_url=rpc_url,
            pubsub_url=pubsub_url,
        )
        return self.connection_target

    async def enable_registrations(self, request: RegistrationRequest) -> dict[str, Any]:
        target_config = self.set_connection_target(request)
        result: dict[str, Any] = {}
        if request.target in ("rpc", "all"):
            result["rpc"] = await self.enable_rpc_registration()
        if request.target in ("pubsub", "all"):
            result["pubsub"] = await self.enable_pubsub_registration()
        result["connection_target"] = {
            "remote_name": target_config.remote_name,
            "rpc_url": target_config.rpc_url,
            "pubsub_url": target_config.pubsub_url,
        }
        return result

    async def disable_registrations(self, target: Literal["rpc", "pubsub", "all"]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if target in ("rpc", "all"):
            result["rpc"] = await self.disable_rpc_registration()
        if target in ("pubsub", "all"):
            result["pubsub"] = await self.disable_pubsub_registration()
        return result

    async def enable_rpc_registration(self) -> dict[str, Any]:
        if self.rpc_registration_enabled and self.rpc_task is not None and not self.rpc_task.done():
            return {"enabled": True, "status": "already_running"}

        self.rpc_registration_enabled = True
        self.rpc_task = asyncio.create_task(
            self.maintain_rpc_client(),
            name=f"{self.config.name}-rpc",
        )
        return {"enabled": True, "status": "started"}

    async def disable_rpc_registration(self) -> dict[str, Any]:
        self.rpc_registration_enabled = False
        task = self.rpc_task
        self.rpc_task = None

        if self.rpc_client is not None:
            try:
                await asyncio.wait_for(self.rpc_client.close(), timeout=2)
            except Exception as exc:
                self.logger.warning("RPC client close did not finish cleanly: %s", exc)
            finally:
                self.rpc_client = None

        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2)
            except Exception as exc:
                self.logger.warning("RPC task cancellation timed out: %s", exc)

        return {"enabled": False, "status": "stopped"}

    async def enable_pubsub_registration(self) -> dict[str, Any]:
        if self.pubsub_registration_enabled and self.pubsub_task is not None and not self.pubsub_task.done():
            return {"enabled": True, "status": "already_running"}

        self.pubsub_registration_enabled = True
        self.pubsub_task = asyncio.create_task(
            self.maintain_pubsub_client(),
            name=f"{self.config.name}-pubsub",
        )
        return {"enabled": True, "status": "started"}

    async def disable_pubsub_registration(self) -> dict[str, Any]:
        self.pubsub_registration_enabled = False
        task = self.pubsub_task
        self.pubsub_task = None

        if self.pubsub_client is not None:
            try:
                await asyncio.wait_for(self.pubsub_client.disconnect(), timeout=2)
            except Exception as exc:
                self.logger.warning("PubSub client disconnect did not finish cleanly: %s", exc)
            finally:
                self.pubsub_client = None

        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2)
            except Exception as exc:
                self.logger.warning("PubSub task cancellation timed out: %s", exc)

        return {"enabled": False, "status": "stopped"}

    async def on_rpc_connect(self, channel: RpcChannel) -> None:
        self.incoming_rpc_channels[channel.id] = channel
        self.logger.info("RPC client connected: %s", channel.id)

    async def on_rpc_disconnect(self, channel: RpcChannel) -> None:
        self.incoming_rpc_channels.pop(channel.id, None)
        self.logger.info("RPC client disconnected: %s", channel.id)

    async def on_pubsub_event(self, *, data: Any, topic: str) -> None:
        event = {
            "topic": topic,
            "data": data,
            "received_by": self.config.name,
        }
        self.received_pubsub_events.appendleft(event)
        self.logger.info("PubSub event received: %s", event)

    async def maintain_rpc_client(self) -> None:
        while not self.shutdown_event.is_set() and self.rpc_registration_enabled:
            target = self.connection_target
            if target is None or target.rpc_url is None:
                self.logger.warning("RPC registration skipped because connection target is incomplete")
                break
            try:
                async with WebSocketRpcClient(
                    f"{target.rpc_url}?token={self.config.outbound_token}",
                    NodeRpcClientMethods(self),
                    retry_config=False,
                    default_response_timeout=10,
                    keep_alive=20,
                    open_timeout=3,
                ) as client:
                    self.rpc_client = client
                    await client.wait_on_rpc_ready()
                    self.logger.info("Connected to peer RPC: %s", target.rpc_url)
                    await client.wait_on_reader()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning("RPC reconnect scheduled: %s", exc)
            finally:
                self.rpc_client = None
            if not self.rpc_registration_enabled or self.shutdown_event.is_set():
                break
            await asyncio.sleep(2)

    async def maintain_pubsub_client(self) -> None:
        while not self.shutdown_event.is_set() and self.pubsub_registration_enabled:
            target = self.connection_target
            if target is None or target.pubsub_url is None:
                self.logger.warning("PubSub registration skipped because connection target is incomplete")
                break
            client = PubSubClient(
                server_uri=f"{target.pubsub_url}?token={self.config.outbound_token}",
                retry_config=False,
                keep_alive=20,
                open_timeout=3,
            )
            client.subscribe(self.config.pubsub_topic, self.on_pubsub_event)
            self.pubsub_client = client
            try:
                async with client:
                    await client.wait_until_ready()
                    self.logger.info(
                        "Connected to peer PubSub: %s", target.pubsub_url
                    )
                    await client.wait_until_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning("PubSub reconnect scheduled: %s", exc)
            finally:
                self.pubsub_client = None
            if not self.pubsub_registration_enabled or self.shutdown_event.is_set():
                break
            await asyncio.sleep(2)

    def verify_token(self, token: str) -> str | None:
        return self.config.accepted_tokens.get(token)

    def health_payload(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "rpc_registration_enabled": self.rpc_registration_enabled,
            "pubsub_registration_enabled": self.pubsub_registration_enabled,
            "rpc_connected": self.rpc_client is not None,
            "pubsub_connected": self.pubsub_client is not None,
            "connection_target": (
                None
                if self.connection_target is None
                else {
                    "remote_name": self.connection_target.remote_name,
                    "rpc_url": self.connection_target.rpc_url,
                    "pubsub_url": self.connection_target.pubsub_url,
                    "outbound_token": self.config.outbound_token,
                }
            ),
            "incoming_rpc_channels": sorted(self.incoming_rpc_channels.keys()),
            "job_count": len(self.jobs),
            "alert_count": len(self.alerts),
            "recent_pubsub_events": list(self.received_pubsub_events),
        }


class NodeRpcServerMethods(RpcMethodsBase):
    def __init__(self, runtime: NodeRuntime):
        super().__init__()
        self.runtime = runtime

    async def get_node_info(self) -> dict:
        return {
            "node": self.runtime.config.name,
            "host": self.runtime.config.host,
            "port": self.runtime.config.port,
            "rpc_connected": self.runtime.rpc_client is not None,
            "pubsub_connected": self.runtime.pubsub_client is not None,
            "incoming_rpc_channels": sorted(self.runtime.incoming_rpc_channels.keys()),
            "job_count": len(self.runtime.jobs),
            "alert_count": len(self.runtime.alerts),
            "recent_pubsub_count": len(self.runtime.received_pubsub_events),
        }

    async def submit_job(self, message: str, sender: str) -> dict:
        job = {
            "id": len(self.runtime.jobs) + 1,
            "description": message,
            "submitted_by": sender,
            "handled_by": self.runtime.config.name,
            "status": "queued",
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.runtime.jobs.appendleft(job)
        return {
            "accepted": True,
            "job": job,
            "job_count": len(self.runtime.jobs),
        }

    async def list_jobs(self) -> dict:
        return {
            "node": self.runtime.config.name,
            "job_count": len(self.runtime.jobs),
            "jobs": list(self.runtime.jobs),
        }


class NodeRpcClientMethods(RpcMethodsBase):
    def __init__(self, runtime: NodeRuntime):
        super().__init__()
        self.runtime = runtime

    async def client_status(self) -> dict:
        target = self.runtime.connection_target
        return {
            "node": self.runtime.config.name,
            "host": self.runtime.config.host,
            "port": self.runtime.config.port,
            "rpc_registration_enabled": self.runtime.rpc_registration_enabled,
            "pubsub_registration_enabled": self.runtime.pubsub_registration_enabled,
            "rpc_connected": self.runtime.rpc_client is not None,
            "pubsub_connected": self.runtime.pubsub_client is not None,
            "connection_target": (
                None
                if target is None
                else {
                    "remote_name": target.remote_name,
                    "rpc_url": target.rpc_url,
                    "pubsub_url": target.pubsub_url,
                }
            ),
            "incoming_rpc_channels": sorted(self.runtime.incoming_rpc_channels.keys()),
            "recent_pubsub_count": len(self.runtime.received_pubsub_events),
            "recent_pubsub_topics": [
                event["topic"] for event in list(self.runtime.received_pubsub_events)[:5]
            ],
            "recent_pubsub_events": list(self.runtime.received_pubsub_events)[:5],
            "alert_count": len(self.runtime.alerts),
            "recent_alerts": list(self.runtime.alerts)[:5],
        }

    async def push_alert(self, message: str, requested_by: str) -> dict:
        alert = {
            "id": len(self.runtime.alerts) + 1,
            "message": message,
            "requested_by": requested_by,
            "received_by": self.runtime.config.name,
            "severity": "info",
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.runtime.alerts.appendleft(alert)
        return {
            "accepted": True,
            "alert": alert,
            "alert_count": len(self.runtime.alerts),
        }

    async def list_alerts(self) -> dict:
        return {
            "node": self.runtime.config.name,
            "alert_count": len(self.runtime.alerts),
            "alerts": list(self.runtime.alerts),
        }


def require_rpc_client(runtime: NodeRuntime) -> WebSocketRpcClient:
    if runtime.rpc_client is None:
        raise HTTPException(status_code=503, detail="Peer RPC client is not connected yet")
    return runtime.rpc_client


def require_peer_channel(runtime: NodeRuntime) -> RpcChannel:
    target = runtime.connection_target
    if target is None:
        raise HTTPException(
            status_code=503,
            detail="Connection target is not configured yet",
        )
    channel = runtime.incoming_rpc_channels.get(target.remote_name)
    if channel is None:
        raise HTTPException(
            status_code=503,
            detail="Peer RPC channel is not connected yet",
        )
    return channel


def rpc_method_catalog() -> dict[str, list[dict[str, Any]]]:
    return {
        "server": [
            {
                "name": "get_node_info",
                "summary": "相手ノードの接続状態と件数情報を取得する",
                "params": [],
                "ui_message_field": False,
            },
            {
                "name": "submit_job",
                "summary": "相手サーバにジョブを登録する",
                "params": ["message", "sender"],
                "ui_message_field": True,
            },
            {
                "name": "list_jobs",
                "summary": "相手サーバに登録されたジョブ一覧を取得する",
                "params": [],
                "ui_message_field": False,
            }
        ],
        "client": [
            {
                "name": "push_alert",
                "summary": "相手 callback 側へアラートを送る",
                "params": ["message", "requested_by"],
                "ui_message_field": True,
            },
            {
                "name": "list_alerts",
                "summary": "相手 callback 側に届いたアラート一覧を取得する",
                "params": [],
                "ui_message_field": False,
            },
            {
                "name": "client_status",
                "summary": "相手ノードの callback 側の詳細状態を取得する",
                "params": [],
                "ui_message_field": False,
            },
        ],
    }


def create_app(config: NodeConfig) -> FastAPI:
    log_path = configure_file_logging(config.name)
    runtime = NodeRuntime(config)
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime.logger.info("File logging enabled: %s", log_path)
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title=f"{config.name} sample app",
        description=(
            "FastAPI の `/docs` から RPC / PubSub の登録、呼び出し、配信を操作するためのサンプルです。"
            " まず `/registration/connect` で接続先 URL を指定して登録を開始してください。"
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.runtime = runtime

    @app.get(
        "/",
        tags=["meta"],
        summary="アプリ概要を取得",
        description="このノードの名前、`/docs` の場所、主なエンドポイント一覧を返します。",
    )
    async def root() -> dict[str, Any]:
        return {
            "name": config.name,
            "docs_url": "/docs",
            "ui_url": "/ui",
            "sample_endpoints": {
                "health": "/health",
                "registration_connect": "/registration/connect",
                "registration_connect_mutual": "/registration/connect-mutual",
                "registration_disconnect_mutual": "/registration/disconnect-mutual",
                "registration_disconnect": "/registration/disconnect",
                "ui_remote_snapshot": "/ui/remote-snapshot",
                "rpc_methods": "/rpc/methods",
                "rpc_server_node_info": "/rpc/server/get-node-info",
                "rpc_server_submit_job": "/rpc/server/submit-job",
                "rpc_server_list_jobs": "/rpc/server/list-jobs",
                "rpc_client_push_alert": "/rpc/client/push-alert",
                "rpc_client_list_alerts": "/rpc/client/list-alerts",
                "rpc_client_status": "/rpc/client/status",
                "pubsub_publish": "/pubsub/publish",
                "pubsub_events": "/pubsub/events",
            },
        }

    @app.get(
        "/ui",
        tags=["ui"],
        summary="簡易操作 UI",
        description="登録、相互登録、相互解除、RPC、PubSub、状態確認を 1 画面で行う簡易 UI を返します。",
        response_class=HTMLResponse,
    )
    async def ui_dashboard(request: FastAPIRequest) -> HTMLResponse:
        if config.name == "server-a":
            default_remote_base_url = "http://127.0.0.1:60002"
            default_remote_name = "server-b"
        elif config.name == "server-b":
            default_remote_base_url = "http://127.0.0.1:60001"
            default_remote_name = "server-a"
        else:
            default_remote_base_url = ""
            default_remote_name = ""
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "node_name": config.name,
                "local_base_url": runtime.local_base_url,
                "default_remote_base_url": default_remote_base_url,
                "default_remote_name": default_remote_name,
                "rpc_catalog": rpc_method_catalog(),
            },
        )

    @app.get(
        "/health",
        tags=["meta"],
        summary="接続状態を確認",
        description=(
            "登録有効化フラグ、RPC / PubSub の接続状態、受信済みイベント一覧を返します。"
            " `/docs` から登録後の状態確認に使います。"
        ),
    )
    async def health() -> dict[str, Any]:
        return runtime.health_payload()

    @app.post(
        "/ui/remote-snapshot",
        tags=["ui"],
        summary="相手ノードの状態を取得",
        description="現在ノード経由で相手ノードの `/health` と `/pubsub/events` を取得します。UI 用の補助 API です。",
    )
    async def ui_remote_snapshot(request: RemoteSnapshotRequest) -> dict[str, Any]:
        remote_health = await runtime.request_remote_json(request.remote_base_url, "/health")
        remote_events = await runtime.request_remote_json(request.remote_base_url, "/pubsub/events")
        return {
            "remote_health": remote_health,
            "remote_events": remote_events,
        }

    @app.get(
        "/rpc/methods",
        tags=["rpc"],
        summary="公開中の sample RPC method 一覧を取得",
        description=(
            "このサンプルで現在公開している RPC method 一覧を返します。"
            " `server` は相手サーバが公開する method、`client` は相手が callback として公開する method です。"
        ),
    )
    async def get_rpc_methods() -> dict[str, Any]:
        return {
            "name": config.name,
            "methods": rpc_method_catalog(),
        }

    @app.post(
        "/registration/connect",
        tags=["registration"],
        summary="peer への登録を開始",
        description=(
            "RPC / PubSub のクライアント登録を開始します。"
            " 接続先は固定ではなく、`/docs` から `remote_name`、`rpc_url`、`pubsub_url` を指定します。"
            " 認証トークンはユーザ入力ではなく、このサーバの設定値を自動利用します。"
            " 同一内容の再登録や、登録中の接続先変更は受け付けません。"
        ),
    )
    async def connect_registration(request: RegistrationRequest) -> dict[str, Any]:
        result = await runtime.enable_registrations(request)
        return {
            "name": config.name,
            "target": request.target,
            "result": result,
            "health": runtime.health_payload(),
        }

    @app.post(
        "/registration/connect-mutual",
        tags=["registration"],
        summary="相手ノードと相互登録する",
        description=(
            "このノード自身の登録を開始したうえで、相手ノードの `/registration/connect` も呼び出します。"
            " 片側だけ登録して PubSub が届かない状況を避けるためのエンドポイントです。"
        ),
    )
    async def connect_mutual_registration(request: MutualRegistrationRequest) -> dict[str, Any]:
        local_request = RegistrationRequest(
            target=request.target,
            remote_name=request.remote_name,
            rpc_url=runtime.base_url_to_ws_url(request.remote_base_url, "/ws/rpc"),
            pubsub_url=runtime.base_url_to_ws_url(request.remote_base_url, "/ws/pubsub"),
        )
        local_result = await runtime.enable_registrations(local_request)

        remote_request = {
            "target": request.target,
            "remote_name": config.name,
            "rpc_url": runtime.local_rpc_ws_url,
            "pubsub_url": runtime.local_pubsub_ws_url,
        }
        remote_result = await runtime.request_remote_json(
            request.remote_base_url,
            "/registration/connect",
            method="POST",
            payload=remote_request,
        )

        return {
            "name": config.name,
            "target": request.target,
            "local_result": local_result,
            "remote_result": remote_result,
            "health": runtime.health_payload(),
        }

    @app.post(
        "/registration/disconnect-mutual",
        tags=["registration"],
        summary="相手ノードと相互解除する",
        description=(
            "このノード自身の登録解除を実行したうえで、相手ノードの `/registration/disconnect` も呼び出します。"
        ),
    )
    async def disconnect_mutual_registration(request: MutualDisconnectRequest) -> dict[str, Any]:
        local_result = await runtime.disable_registrations(request.target)
        remote_result = await runtime.request_remote_json(
            request.remote_base_url,
            "/registration/disconnect",
            method="POST",
            payload={"target": request.target},
        )
        return {
            "name": config.name,
            "target": request.target,
            "local_result": local_result,
            "remote_result": remote_result,
            "health": runtime.health_payload(),
        }

    @app.post(
        "/registration/disconnect",
        tags=["registration"],
        summary="peer への登録を解除",
        description=(
            "RPC / PubSub のクライアント登録を停止します。"
            " `/docs` から接続ループを止めたいときに使用します。"
        ),
    )
    async def disconnect_registration(request: RegistrationRequest) -> dict[str, Any]:
        result = await runtime.disable_registrations(request.target)
        return {
            "name": config.name,
            "target": request.target,
            "result": result,
            "health": runtime.health_payload(),
        }

    @app.post(
        "/rpc/server/get-node-info",
        tags=["rpc"],
        summary="相手サーバのノード情報を取得",
        description=(
            "peer 側 FastAPI が公開している server method `get_node_info` を呼び出します。"
            " 実行前に `/registration/connect` で RPC 登録を有効化してください。"
        ),
    )
    async def rpc_server_get_node_info() -> dict[str, Any]:
        client = require_rpc_client(runtime)
        response = await client.other.get_node_info()
        return {
            "from": config.name,
            "to": runtime.connection_target.remote_name if runtime.connection_target else None,
            "rpc_result": response.result,
        }

    @app.post(
        "/rpc/server/submit-job",
        tags=["rpc"],
        summary="相手サーバへジョブを登録",
        description=(
            "peer 側 FastAPI が公開している server method `submit_job` を呼び出し、"
            " 相手サーバにジョブを登録します。"
        ),
    )
    async def rpc_server_submit_job(request: RpcCallRequest) -> dict[str, Any]:
        client = require_rpc_client(runtime)
        response = await client.other.submit_job(
            message=request.message,
            sender=config.name,
        )
        return {
            "from": config.name,
            "to": runtime.connection_target.remote_name if runtime.connection_target else None,
            "rpc_result": response.result,
        }

    @app.post(
        "/rpc/server/list-jobs",
        tags=["rpc"],
        summary="相手サーバのジョブ一覧を取得",
        description=(
            "peer 側 FastAPI が公開している server method `list_jobs` を呼び出し、"
            " 相手サーバに登録されたジョブ一覧を取得します。"
        ),
    )
    async def rpc_server_list_jobs() -> dict[str, Any]:
        client = require_rpc_client(runtime)
        response = await client.other.list_jobs()
        return {
            "from": config.name,
            "to": runtime.connection_target.remote_name if runtime.connection_target else None,
            "rpc_result": response.result,
        }

    @app.post(
        "/rpc/client/push-alert",
        tags=["rpc"],
        summary="相手 callback 側へアラートを送る",
        description=(
            "peer 側 FastAPI が callback として公開している `push_alert` を呼び出し、"
            " 相手 callback 側のアラート一覧へ追加します。"
        ),
    )
    async def rpc_client_push_alert(request: RpcCallRequest) -> dict[str, Any]:
        channel = require_peer_channel(runtime)
        response = await channel.other.push_alert(
            message=request.message,
            requested_by=config.name,
        )
        return {
            "from": config.name,
            "to": runtime.connection_target.remote_name if runtime.connection_target else None,
            "rpc_result": response.result,
        }

    @app.post(
        "/rpc/client/list-alerts",
        tags=["rpc"],
        summary="相手 callback 側のアラート一覧を取得",
        description=(
            "peer 側 FastAPI が callback として公開している `list_alerts` を呼び出し、"
            " 相手 callback 側のアラート一覧を取得します。"
        ),
    )
    async def rpc_client_list_alerts() -> dict[str, Any]:
        channel = require_peer_channel(runtime)
        response = await channel.other.list_alerts()
        return {
            "from": config.name,
            "to": runtime.connection_target.remote_name if runtime.connection_target else None,
            "rpc_result": response.result,
        }

    @app.post(
        "/rpc/client/status",
        tags=["rpc"],
        summary="相手 callback 側の詳細状態を取得",
        description=(
            "peer 側 FastAPI が callback として公開している `client_status` を呼び出し、"
            " 相手ノードの callback 側の詳細状態を取得します。"
        ),
    )
    async def rpc_client_status() -> dict[str, Any]:
        channel = require_peer_channel(runtime)
        response = await channel.other.client_status()
        return {
            "from": config.name,
            "to": runtime.connection_target.remote_name if runtime.connection_target else None,
            "rpc_result": response.result,
        }

    @app.post(
        "/pubsub/publish",
        tags=["pubsub"],
        summary="PubSub メッセージを publish",
        description=(
            "指定トピックにメッセージを publish します。"
            " peer 側で `/pubsub/events` を確認すると受信結果を見られます。"
        ),
    )
    async def publish_message(request: PubSubPublishRequest) -> dict[str, Any]:
        payload = {
            "source": config.name,
            "message": request.message,
        }
        await runtime.pubsub_endpoint.publish(request.topic, payload)
        return {
            "published_by": config.name,
            "topic": request.topic,
            "payload": payload,
        }

    @app.get(
        "/pubsub/events",
        tags=["pubsub"],
        summary="受信済み PubSub イベントを取得",
        description="このノードが受信した PubSub イベント一覧を返します。",
    )
    async def pubsub_events() -> dict[str, Any]:
        return {
            "name": config.name,
            "events": list(runtime.received_pubsub_events),
        }

    @app.websocket("/ws/rpc")
    async def rpc_websocket(websocket: WebSocket, token: str = Query(...)) -> None:
        peer_id = runtime.verify_token(token)
        if peer_id is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await runtime.rpc_endpoint.main_loop(
            websocket,
            channel_id=peer_id,
            authenticated_peer=peer_id,
        )

    @app.websocket("/ws/pubsub")
    async def pubsub_websocket(websocket: WebSocket, token: str = Query(...)) -> None:
        peer_id = runtime.verify_token(token)
        if peer_id is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await runtime.pubsub_endpoint.main_loop(
            websocket,
            channel_id=peer_id,
            authenticated_peer=peer_id,
        )

    return app
