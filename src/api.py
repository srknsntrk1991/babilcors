import asyncio
import base64
import time
from typing import Dict, Callable, Awaitable, Any, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .iot import IoTRelay

try:
    from .shadow import DeviceShadowStore
except Exception:
    DeviceShadowStore = None

from .diagnostics import build_base_summary, compute_alerts


def build_app(
    relays: Dict[str, IoTRelay],
    shadow: "DeviceShadowStore | None" = None,
    snapshot_provider: "Callable[[], Awaitable[List[Dict[str, Any]]]] | None" = None,
    events_provider: "Callable[[int], List[Dict[str, Any]]] | None" = None,
) -> FastAPI:
    app = FastAPI(title="BabilCORS IoT API")

    @app.websocket("/api/v1/ws/{mountpoint}")
    async def ws_stream(ws: WebSocket, mountpoint: str):
        await ws.accept()
        relay = relays.get(mountpoint)
        if relay is None:
            await ws.close(code=1008)
            return
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        relay.add_websocket_queue(q)
        try:
            while True:
                data = await q.get()
                await ws.send_bytes(data)
        except WebSocketDisconnect:
            pass
        finally:
            relay.remove_websocket_queue(q)

    @app.get("/api/v1/stream/{mountpoint}")
    async def sse_stream(mountpoint: str):
        relay = relays.get(mountpoint)
        if relay is None:
            async def gen_nf():
                yield "event: error\ndata: mountpoint_not_found\n\n"
            return StreamingResponse(gen_nf(), media_type="text/event-stream")
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        relay.add_websocket_queue(q)

        async def gen():
            try:
                while True:
                    data = await q.get()
                    b64 = base64.b64encode(data).decode("ascii")
                    yield f"event: rtcm\ndata: {b64}\n\n"
            finally:
                relay.remove_websocket_queue(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/v1/devices/{device_id}/shadow")
    async def get_shadow(device_id: str):
        if shadow is None:
            return {"ok": False, "msg": "shadow_disabled"}
        doc = await shadow.get(device_id)
        return {"ok": True, "shadow": doc}

    @app.get("/api/v1/devices")
    async def search_devices(query: str = "", limit: int = 100):
        if shadow is None:
            return {"ok": False, "msg": "shadow_disabled"}
        res = await shadow.search(query=query, limit=limit)
        return {"ok": True, **res}

    @app.get("/api/v1/devices/{device_id}/history")
    async def get_history(device_id: str, start_ms: int | None = None, end_ms: int | None = None, limit: int = 100, reverse: bool = True):
        if shadow is None:
            return {"ok": False, "msg": "shadow_disabled"}
        res = await shadow.history(device_id, start_ms=start_ms, end_ms=end_ms, limit=limit, reverse=reverse)
        return {"ok": True, **res}

    @app.get("/api/v1/health")
    async def health():
        snaps = await snapshot_provider() if snapshot_provider else []
        return {
            "ok": True,
            "ts_unix_ms": int(time.time() * 1000),
            "mountpoints": len(snaps),
            "mqtt_enabled": any(True for _ in relays.values()),
        }

    @app.get("/api/v1/bases")
    async def bases(detail: bool = False):
        snaps = await snapshot_provider() if snapshot_provider else []
        if detail:
            return {"items": snaps}
        return {"items": [build_base_summary(s) for s in snaps]}

    @app.get("/api/v1/bases/{mountpoint}")
    async def base_one(mountpoint: str):
        snaps = await snapshot_provider() if snapshot_provider else []
        for s in snaps:
            if str(s.get("mountpoint") or "") == mountpoint:
                return {"ok": True, "base": s, "summary": build_base_summary(s)}
        return {"ok": False, "msg": "not_found"}

    @app.get("/api/v1/alerts")
    async def alerts(mountpoint: str = "", user: str = "", severity: str = ""):
        snaps = await snapshot_provider() if snapshot_provider else []
        all_alerts = compute_alerts(snaps)
        out = []
        for a in all_alerts:
            if mountpoint and a.mountpoint != mountpoint:
                continue
            if user and (a.user or "") != user:
                continue
            if severity and a.severity != severity:
                continue
            out.append({"code": a.code, "severity": a.severity, "message": a.message, "mountpoint": a.mountpoint, "conn_id": a.conn_id, "user": a.user, "ctx": a.ctx or {}})
        return {"items": out}

    @app.get("/api/v1/events")
    async def events(limit: int = 200):
        if events_provider is None:
            return {"items": []}
        return {"items": events_provider(limit)}

    return app
