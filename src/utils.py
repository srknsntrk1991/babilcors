from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import os
import binascii
from urllib.parse import parse_qs, urlsplit
import time
from dataclasses import dataclass
from typing import Optional, Iterable, List, Tuple


def now_monotonic() -> float:
    return time.monotonic()


def b64decode_str(s: str) -> bytes:
    try:
        return base64.b64decode(s, validate=True)
    except binascii.Error:
        return b""


def safe_decode(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class NetAddr:
    host: str
    port: int


def format_addr(peername: object) -> str:
    if isinstance(peername, tuple) and len(peername) >= 2:
        return f"{peername[0]}:{peername[1]}"
    return "unknown"


def compile_ip_nets(values: Iterable[str]) -> List[ipaddress._BaseNetwork]:
    out: List[ipaddress._BaseNetwork] = []
    for v in values:
        s = str(v).strip()
        if not s:
            continue
        if "/" in s:
            out.append(ipaddress.ip_network(s, strict=False))
        else:
            out.append(ipaddress.ip_network(s + "/32", strict=False))
    return out


def ip_allowed(client_ip: str, allow: List[ipaddress._BaseNetwork], deny: List[ipaddress._BaseNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(client_ip)
    except Exception:
        return len(allow) == 0
    for n in deny:
        if ip in n:
            return False
    if not allow:
        return True
    for n in allow:
        if ip in n:
            return True
    return False


def gen_trace_id() -> str:
    return binascii.hexlify(os.urandom(16)).decode("ascii")


def gen_span_id() -> str:
    return binascii.hexlify(os.urandom(8)).decode("ascii")


def build_traceparent(trace_id: str, span_id: str) -> str:
    return f"00-{trace_id}-{span_id}-01"


def split_path_query(path: str) -> tuple[str, dict[str, str]]:
    parts = urlsplit(path)
    q = parse_qs(parts.query, keep_blank_values=True)
    out: dict[str, str] = {}
    for k, v in q.items():
        if not v:
            out[k] = ""
        else:
            out[k] = v[-1]
    return parts.path or path, out


# WGS84 constants
_WGS84_A = 6378137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = 2 * _WGS84_F - _WGS84_F * _WGS84_F


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    # Bowring method
    import math
    a = _WGS84_A
    e2 = _WGS84_E2
    b = a * (1 - _WGS84_F)
    ep2 = (a * a - b * b) / (b * b)
    p = (x * x + y * y) ** 0.5
    if p == 0:
        # pole
        lat = 90.0 if z > 0 else -90.0
        lon = 0.0
        h = abs(z) - b
        return lat, lon, h
    th = math.atan2(z * a, p * b)
    lon = math.atan2(y, x)
    lat = math.atan2(z + ep2 * b * math.sin(th) ** 3, p - e2 * a * math.cos(th) ** 3)
    sin_lat = math.sin(lat)
    N = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
    h = p / math.cos(lat) - N
    return math.degrees(lat), math.degrees(lon), h


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def read_line(
    reader: asyncio.StreamReader,
    *,
    limit: int,
    timeout_s: float,
) -> Optional[bytes]:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
    except TimeoutError:
        return None
    if not line:
        return None
    if len(line) > limit:
        return None
    return line


async def read_http_headers(
    reader: asyncio.StreamReader,
    *,
    first_line: bytes,
    max_total_bytes: int = 64 * 1024,
    line_limit: int = 8192,
    timeout_s: float = 5.0,
) -> Optional[bytes]:
    buf = bytearray()
    buf.extend(first_line)
    total = len(buf)
    while True:
        line = await read_line(reader, limit=line_limit, timeout_s=timeout_s)
        if line is None:
            return None
        buf.extend(line)
        total += len(line)
        if total > max_total_bytes:
            return None
        if line in (b"\r\n", b"\n"):
            return bytes(buf)
