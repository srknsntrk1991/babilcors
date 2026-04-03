import json
import time
from typing import Any, Dict, Optional

try:
    import redis.asyncio as redis  # type: ignore
except Exception:
    redis = None


class DeviceShadowStore:
    def __init__(self, *, url: str = "", ttl_s: int = 86400):
        self.url = url
        self.ttl_s = int(ttl_s)
        self._r = None
        self._mem: Dict[str, Dict[str, Any]] = {}
        self._mem_hist: Dict[str, list[Dict[str, Any]]] = {}
        self._hist_maxlen = 10_000

    async def start(self) -> None:
        if not self.url:
            return
        if self.url == "memory":
            return
        if redis is None:
            return
        if self._r is None:
            self._r = redis.from_url(self.url, decode_responses=True)

    async def close(self) -> None:
        if self._r is not None:
            try:
                await self._r.close()
            except Exception:
                pass
            self._r = None

    async def upsert(self, device_id: str, payload: Dict[str, Any]) -> None:
        ts_ms = int(time.time() * 1000)
        if self.url == "memory":
            cur = dict(self._mem.get(device_id) or {})
            cur.update(payload)
            cur["updated_unix_ms"] = ts_ms
            self._mem[device_id] = cur
            if "lat" in payload and "lon" in payload:
                row = {"ts_unix_ms": ts_ms, "lat": payload.get("lat"), "lon": payload.get("lon"), "shadow": dict(cur)}
                h = self._mem_hist.setdefault(device_id, [])
                h.append(row)
                if len(h) > self._hist_maxlen:
                    del h[: len(h) - self._hist_maxlen]
            return
        if self._r is None:
            return
        key = f"shadow:{device_id}"
        raw = await self._r.get(key)
        cur: Dict[str, Any] = {}
        if raw:
            try:
                cur = json.loads(raw)
            except Exception:
                cur = {}
        cur.update(payload)
        cur["updated_unix_ms"] = ts_ms
        await self._r.set(key, json.dumps(cur, ensure_ascii=False))
        await self._r.expire(key, self.ttl_s)
        if "lat" in payload and "lon" in payload:
            try:
                stream = f"shadowhist:{device_id}"
                await self._r.xadd(stream, {"doc": json.dumps({"lat": payload.get("lat"), "lon": payload.get("lon"), "shadow": cur}, ensure_ascii=False)}, id=f"{ts_ms}-0")
                await self._r.xtrim(stream, maxlen=10000, approximate=True)
            except Exception:
                pass

    async def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        if self.url == "memory":
            return dict(self._mem.get(device_id) or {}) or None
        if self._r is None:
            return None
        key = f"shadow:{device_id}"
        raw = await self._r.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def search(self, query: str, limit: int = 100) -> Dict[str, Any]:
        if self.url == "memory":
            items = []
            total = 0
            for k, v in self._mem.items():
                if query and not k.startswith(query):
                    continue
                total += 1
                if len(items) < max(1, min(limit, 1000)):
                    items.append({"id": k, "shadow": v})
            return {"items": items, "total": total}
        if self._r is None:
            return {"items": [], "total": 0}
        pattern = f"shadow:{query}*" if query else "shadow:*"
        items = []
        total = 0
        try:
            async for key in self._r.scan_iter(match=pattern, count=1000):
                total += 1
                if len(items) < max(1, min(limit, 1000)):
                    try:
                        raw = await self._r.get(key)
                        doc = json.loads(raw) if raw else None
                        items.append({"id": key.split(":",1)[1] if ":" in key else key, "shadow": doc})
                    except Exception:
                        pass
        except Exception:
            pass
        return {"items": items, "total": total}

    async def history(self, device_id: str, *, start_ms: Optional[int] = None, end_ms: Optional[int] = None, limit: int = 100, reverse: bool = True) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 100), 5000))
        if self.url == "memory":
            h = list(self._mem_hist.get(device_id) or [])
            if start_ms is not None:
                h = [x for x in h if int(x.get("ts_unix_ms") or 0) >= int(start_ms)]
            if end_ms is not None:
                h = [x for x in h if int(x.get("ts_unix_ms") or 0) <= int(end_ms)]
            if reverse:
                h = list(reversed(h))
            return {"items": h[:limit], "total": len(h)}
        if self._r is None:
            return {"items": [], "total": 0}
        stream = f"shadowhist:{device_id}"
        start_id = f"{int(start_ms)}-0" if start_ms is not None else "-"
        end_id = f"{int(end_ms)}-0" if end_ms is not None else "+"
        try:
            if reverse:
                rows = await self._r.xrevrange(stream, max=end_id, min=start_id, count=limit)
            else:
                rows = await self._r.xrange(stream, min=start_id, max=end_id, count=limit)
        except Exception:
            rows = []
        items = []
        for sid, fields in rows:
            try:
                doc = json.loads(fields.get("doc") or "{}")
            except Exception:
                doc = {"raw": fields}
            items.append({"id": sid, **doc})
        return {"items": items, "total": len(items)}
