from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
import hashlib
import os
import ssl
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional, Set, Tuple

from .auth import UserRecord, load_users, parse_basic_auth, user_can_access, verify_password
from .nmea import parse_gga, parse_gsa, parse_gsv, parse_rmc, parse_vtg, parse_zda
from .sourcetable import SourcetableInfo, build_sourcetable
from .tiers import Tier, TokenBucket, build_tier, epoch_gate_ok
from .utils import build_traceparent, compile_ip_nets, ecef_to_geodetic, format_addr, gen_span_id, gen_trace_id, haversine_m, ip_allowed, now_monotonic, read_http_headers, read_line, safe_decode, split_path_query
from .iot import MQTTManager, ProtobufSerializer, IoTRelay
from .api import build_app
from .shadow import DeviceShadowStore
from .geofence import point_in_polygon, polygon_bbox, bbox_intersects, geojson_to_rings, rings_to_geojson_polygon
from .jwt_auth import JwtManager
from .rtcm import RtcmStreamParser, parse_rtcm_1033, parse_rtcm_1005_1006, parse_rtcm_1007_1008
try:
    from jsonschema import validate as js_validate, ValidationError as JSValidationError
except Exception:
    JSValidationError = Exception  # fallback if jsonschema not installed


@dataclass(frozen=True)
class ListenCfg:
    host: str
    port: int
    backlog: int
    reuse_port: bool
    tls_certfile: Optional[str]
    tls_keyfile: Optional[str]
    tls_client_ca: Optional[str] = None


@dataclass(frozen=True)
class SourcesCfg:
    password: str
    mountpoints: Tuple[str, ...]


@dataclass(frozen=True)
class LoggingCfg:
    level: str
    fmt: str


@dataclass(frozen=True)
class CasterCfg:
    listen: ListenCfg
    logging: LoggingCfg
    sourcetable: SourcetableInfo
    sources: SourcesCfg
    tiers: Dict[str, Tier]
    users: Dict[str, UserRecord]
    limits: Dict[str, int]
    security: Dict[str, Any]


class ByteQueueFull(Exception):
    pass


class ByteQueue:
    def __init__(self, max_bytes: int):
        self._max = max(0, int(max_bytes))
        self._buf: Deque[bytes] = deque()
        self._bytes = 0
        self._cv = asyncio.Condition()
        self._closed = False

    def close(self) -> None:
        self._closed = True
        async def _notify() -> None:
            async with self._cv:
                self._cv.notify_all()
        asyncio.create_task(_notify())

    def put_nowait(self, b: bytes) -> None:
        if self._closed:
            raise ByteQueueFull()
        if self._max <= 0:
            return
        if len(b) > self._max:
            raise ByteQueueFull()
        if self._bytes + len(b) > self._max:
            raise ByteQueueFull()
        self._buf.append(b)
        self._bytes += len(b)
        async def _notify() -> None:
            async with self._cv:
                self._cv.notify(1)
        asyncio.create_task(_notify())

    async def get(self) -> Optional[bytes]:
        async with self._cv:
            while not self._buf and not self._closed:
                await self._cv.wait()
            if not self._buf:
                return None
            b = self._buf.popleft()
            self._bytes -= len(b)
            return b


@dataclass(eq=False)
class RoverSession:
    id: int
    username: str
    tier: Tier
    mountpoint: str
    addr: str
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    queue: ByteQueue
    bucket: Optional[TokenBucket]
    ep_last_ts: Optional[float] = None
    dropped_bytes: int = 0
    sent_bytes: int = 0
    last_gga: Optional[Tuple[float, float, int, int, float]] = None
    last_gsa: Optional[Tuple[str, int, list, float, float, float]] = None
    last_gsv_snr_mean: Optional[float] = None
    last_rmc: Optional[Tuple[str, float, float, float, float, str]] = None
    last_vtg: Optional[Tuple[float, float]] = None
    last_zda: Optional[Tuple[str, int, int, int]] = None
    last_gsv_total_sv: Optional[int] = None
    _gsv_pending_total: int = 0
    _gsv_pending_last_num: int = 0
    _gsv_pending_sats: list = None
    _gsv_pending_t0: float = 0.0
    _last_nmea_ts_mono: float = 0.0
    _last_lat: Optional[float] = None
    _last_lon: Optional[float] = None
    _last_pos_ts_mono: float = 0.0
    _geofence_violation_ts_mono: float = 0.0
    _jamming_suspect_ts_mono: float = 0.0
    _spoofing_suspect_ts_mono: float = 0.0


class MountpointHub:
    def __init__(self, mountpoint: str, logger: logging.Logger):
        self.mountpoint = mountpoint
        self._logger = logger
        self._source_addr: Optional[str] = None
        self._rovers: Set[RoverSession] = set()
        self._lock = asyncio.Lock()
        self._rx_bytes: int = 0
        self._tx_bytes_total: int = 0
        self._dropped_bytes_total: int = 0
        self._rtcm_frames_total: int = 0
        self._rtcm_crc_errors_total: int = 0
        self._rtcm_msg_counts: Dict[int, int] = {}
        self._rtcm_max_types: int = 64
        self._station_info: Dict[str, Any] = {}
        self._last_rtcmtime_mono: float = 0.0
        self._relay: Optional[IoTRelay] = None

    def set_iot_relay(self, relay: IoTRelay) -> None:
        self._relay = relay

    def record_sent_bytes(self, n: int) -> None:
        if n > 0:
            self._tx_bytes_total += int(n)

    def record_dropped_bytes(self, n: int) -> None:
        if n > 0:
            self._dropped_bytes_total += int(n)

    def record_rtcm_crc_errors(self, n: int) -> None:
        if n > 0:
            self._rtcm_crc_errors_total += int(n)

    def record_rtcm_frame(self, msg_type: int) -> None:
        self._rtcm_frames_total += 1
        self._last_rtcmtime_mono = now_monotonic()
        if msg_type in self._rtcm_msg_counts:
            self._rtcm_msg_counts[msg_type] += 1
            return
        if len(self._rtcm_msg_counts) >= self._rtcm_max_types:
            self._rtcm_msg_counts[0] = self._rtcm_msg_counts.get(0, 0) + 1
            return
        self._rtcm_msg_counts[msg_type] = 1

    def publish_rtcm_frame(self, *, frame: bytes, msg_type: int) -> None:
        if self._relay is None:
            return
        station_id = 0
        try:
            station_id = int(self._station_info.get("station_id") or 0)
        except Exception:
            station_id = 0
        try:
            self._relay.queue.put_nowait((frame, int(msg_type), station_id, int(self._rtcm_crc_errors_total)))
        except Exception:
            pass

    def record_station_info(self, info: Dict[str, Any]) -> None:
        if not info:
            return
        self._station_info.update({k: v for (k, v) in info.items() if v})

    @property
    def source_addr(self) -> Optional[str]:
        return self._source_addr

    @property
    def rover_count(self) -> int:
        return len(self._rovers)

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            rovers = tuple(self._rovers)
            rover_samples = []
            for s in rovers[:20]:
                rover_samples.append(
                    {
                        "conn_id": s.id,
                        "user": s.username,
                        "client_ip": (s.addr.split(":", 1)[0] if s.addr else ""),
                        "sent_bytes": s.sent_bytes,
                        "dropped_bytes": s.dropped_bytes,
                        "gga": s.last_gga,
                        "gsa": s.last_gsa,
                        "gsv_total_sv": s.last_gsv_total_sv,
                        "gsv_snr_mean": s.last_gsv_snr_mean,
                        "rmc": s.last_rmc,
                        "vtg": s.last_vtg,
                        "zda": s.last_zda,
                        "last_nmea_age_s": (now_monotonic() - getattr(s, "_last_nmea_ts_mono", 0.0)) if getattr(s, "_last_nmea_ts_mono", 0.0) else None,
                        "nmea_to_rtcmtime_delta_s": ((self._last_rtcmtime_mono - getattr(s, "_last_nmea_ts_mono", 0.0)) if (self._last_rtcmtime_mono and getattr(s, "_last_nmea_ts_mono", 0.0)) else None),
                        "geofence_violation_recent": (now_monotonic() - getattr(s, "_geofence_violation_ts_mono", 0.0)) < 60.0 if getattr(s, "_geofence_violation_ts_mono", 0.0) else False,
                        "jamming_suspect_recent": (now_monotonic() - getattr(s, "_jamming_suspect_ts_mono", 0.0)) < 60.0 if getattr(s, "_jamming_suspect_ts_mono", 0.0) else False,
                        "spoofing_suspect_recent": (now_monotonic() - getattr(s, "_spoofing_suspect_ts_mono", 0.0)) < 60.0 if getattr(s, "_spoofing_suspect_ts_mono", 0.0) else False,
                    }
                )
            si = dict(self._station_info)
            if all(k in si for k in ("ecef_x_m", "ecef_y_m", "ecef_z_m")):
                try:
                    lat, lon, h = ecef_to_geodetic(float(si["ecef_x_m"]), float(si["ecef_y_m"]), float(si["ecef_z_m"]))
                    si["lat_deg"] = lat; si["lon_deg"] = lon; si["ellipsoidal_h_m"] = h
                except Exception:
                    pass
            return {
                "mountpoint": self.mountpoint,
                "source_attached": self._source_addr is not None,
                "source_addr": self._source_addr,
                "rover_count": len(rovers),
                "rx_bytes_total": self._rx_bytes,
                "tx_bytes_total": self._tx_bytes_total,
                "dropped_bytes_total": self._dropped_bytes_total,
                "rtcm_frames_total": self._rtcm_frames_total,
                "rtcm_crc_errors_total": self._rtcm_crc_errors_total,
                "rtcm_msg_counts": dict(self._rtcm_msg_counts),
                "station_info": si,
                "last_rtcmtime_age_s": (now_monotonic() - self._last_rtcmtime_mono) if self._last_rtcmtime_mono else None,
                "rover_samples": rover_samples,
            }

    async def attach_source(self, addr: str) -> bool:
        async with self._lock:
            if self._source_addr is not None and self._source_addr != addr:
                _log_event(
                    self._logger,
                    logging.INFO,
                    "source_rejected",
                    mountpoint=self.mountpoint,
                    source_addr=addr,
                    addr=self._source_addr,
                )
                return False
            self._source_addr = addr
            _log_event(self._logger, logging.INFO, "source_attached", mountpoint=self.mountpoint, source_addr=addr)
            return True

    async def detach_source(self) -> None:
        async with self._lock:
            _log_event(self._logger, logging.INFO, "source_detached", mountpoint=self.mountpoint, source_addr=self._source_addr or "unknown")
            self._source_addr = None
            rovers = tuple(self._rovers)
        for s in rovers:
            try:
                s.queue.close()
                if not s.writer.is_closing():
                    s.writer.close()
            except Exception:
                pass

    async def add_rover(self, session: RoverSession) -> None:
        async with self._lock:
            self._rovers.add(session)
            ip = session.addr.split(":", 1)[0]
            _log_event(self._logger, logging.INFO, "rover_attached", mountpoint=self.mountpoint, user=session.username, client_ip=ip, conn_id=session.id)

    async def remove_rover(self, session: RoverSession) -> None:
        async with self._lock:
            if session in self._rovers:
                self._rovers.remove(session)
                ip = session.addr.split(":", 1)[0]
                _log_event(self._logger, logging.INFO, "rover_detached", mountpoint=self.mountpoint, user=session.username, client_ip=ip, conn_id=session.id, sent_bytes=session.sent_bytes, dropped_bytes=session.dropped_bytes)

    async def on_source_data(self, chunk: bytes) -> None:
        if not chunk:
            return
        async with self._lock:
            self._rx_bytes += len(chunk)
            rovers = tuple(self._rovers)
        now = now_monotonic()
        for s in rovers:
            if not epoch_gate_ok(s.ep_last_ts, s.tier.max_epochs_per_minute, now):
                continue
            if s.tier.max_epochs_per_minute > 0:
                s.ep_last_ts = now
            try:
                s.queue.put_nowait(chunk)
            except ByteQueueFull:
                s.dropped_bytes += len(chunk)
                self.record_dropped_bytes(len(chunk))


def load_config(path: str) -> CasterCfg:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    listen = raw.get("listen") or {}
    logging_cfg = raw.get("logging") or {}
    st = raw.get("sourcetable") or {}
    sources = raw.get("sources") or {}
    tiers = raw.get("tiers") or {}
    users = raw.get("users") or {}
    limits = raw.get("limits") or {}
    security = raw.get("security") or {}

    listen_cfg = ListenCfg(
        host=str(listen.get("host", "0.0.0.0")),
        port=int(listen.get("port", 2101)),
        backlog=int(listen.get("backlog", 200)),
        reuse_port=bool(listen.get("reuse_port", False)),
        tls_certfile=str(listen.get("tls_certfile", "")) or None,
        tls_keyfile=str(listen.get("tls_keyfile", "")) or None,
        tls_client_ca=str(listen.get("tls_client_ca", "")) or None,
    )
    log_cfg = LoggingCfg(level=str(logging_cfg.get("level", "INFO")), fmt=str(logging_cfg.get("format", "plain")))
    st_info = SourcetableInfo(
        operator=str(st.get("operator", "NTRIP")),
        country=str(st.get("country", "XX")),
        network=str(st.get("network", "")),
        mountpoints_meta=st.get("mountpoints_meta") if isinstance(st.get("mountpoints_meta"), dict) else None,
    )

    src_pwd_raw = str(sources.get("password", ""))
    if src_pwd_raw.startswith("env:"):
        key = src_pwd_raw.split(":", 1)[1]
        src_pwd = os.getenv(key, "")
    else:
        src_pwd = src_pwd_raw
    mps = sources.get("mountpoints", [])
    if isinstance(mps, list):
        mp_tuple = tuple(str(x) for x in mps)
    else:
        mp_tuple = tuple()
    sources_cfg = SourcesCfg(password=src_pwd, mountpoints=mp_tuple)

    tier_map: Dict[str, Tier] = {}
    for name, td in (tiers or {}).items():
        if isinstance(td, dict):
            tier_map[str(name)] = build_tier(str(name), td)
    user_map = load_users(users)

    return CasterCfg(
        listen=listen_cfg,
        logging=log_cfg,
        sourcetable=st_info,
        sources=sources_cfg,
        tiers=tier_map,
        users=user_map,
        limits={k: int(v) for k, v in (limits or {}).items() if isinstance(v, (int, float))},
        security=security,
    )


class ConfigValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_config(cfg: CasterCfg) -> None:
    errors: list[str] = []

    if not isinstance(cfg.listen.port, int) or not (0 <= cfg.listen.port <= 65535):
        errors.append("listen.port 0-65535 aralığında olmalı")
    if not cfg.listen.host or not str(cfg.listen.host).strip():
        errors.append("listen.host boş olamaz")
    if cfg.listen.backlog < 1:
        errors.append("listen.backlog en az 1 olmalı")
    if cfg.listen.tls_certfile or cfg.listen.tls_keyfile:
        if not (cfg.listen.tls_certfile and cfg.listen.tls_keyfile):
            errors.append("TLS için listen.tls_certfile ve listen.tls_keyfile birlikte verilmeli")

    if cfg.sources.password is None or str(cfg.sources.password) == "":
        errors.append("sources.password boş olamaz")

    mps = tuple(cfg.sources.mountpoints or ())
    if mps:
        if len(set(mps)) != len(mps):
            errors.append("sources.mountpoints içinde tekrar eden değerler var")
        for mp in mps:
            if not mp or not str(mp).strip():
                errors.append("sources.mountpoints boş değer içeremez")
                break
            if any(ch.isspace() for ch in str(mp)):
                errors.append(f"sources.mountpoints geçersiz: '{mp}' (boşluk içeremez)")
                break
            if "/" in str(mp):
                errors.append(f"sources.mountpoints geçersiz: '{mp}' ('/' içeremez)")
                break

    for name, t in (cfg.tiers or {}).items():
        if t.rate_limit_bps < 0:
            errors.append(f"tiers.{name}.rate_limit_bps negatif olamaz")
        if t.max_epochs_per_minute < 0:
            errors.append(f"tiers.{name}.max_epochs_per_minute negatif olamaz")
        if t.max_queue_bytes < 0:
            errors.append(f"tiers.{name}.max_queue_bytes negatif olamaz")

    for u in (cfg.users or {}).values():
        if (
            (u.password is None or str(u.password) == "")
            and (not u.password_sha256)
            and (not getattr(u, "password_hash", None))
        ):
            errors.append(f"users.{u.username} için password, password_sha256 veya password_hash gerekli")
            break

    for k, v in (cfg.limits or {}).items():
        if v < 0:
            errors.append(f"limits.{k} negatif olamaz")

    if isinstance(cfg.security, dict):
        try:
            compile_ip_nets(cfg.security.get("ip_allow", []))
            compile_ip_nets(cfg.security.get("ip_deny", []))
        except Exception:
            errors.append("security.ip_allow/ip_deny geçersiz IP veya CIDR içeriyor")

    if cfg.tiers:
        missing: set[str] = set()
        for u in (cfg.users or {}).values():
            if u.tier and u.tier not in cfg.tiers:
                missing.add(u.tier)
        if missing:
            errors.append("users.*.tier tanımsız: " + ", ".join(sorted(missing)))

    if errors:
        raise ConfigValidationError(errors)


class NtripCaster:
    def __init__(self, cfg: CasterCfg, cfg_path: Optional[str] = None):
        self.cfg = cfg
        self._logger = logging.getLogger("caster")
        self._server: Optional[asyncio.AbstractServer] = None
        self._mountpoints: Dict[str, MountpointHub] = {}
        self._id_seq = 0
        self._conn_seq = 0
        self._stats_task: Optional[asyncio.Task] = None
        self._rover_ip_counts: Dict[str, int] = {}
        self._rover_total: int = 0
        self._ip_allow = compile_ip_nets(cfg.security.get("ip_allow", [])) if isinstance(cfg.security, dict) else []
        self._ip_deny = compile_ip_nets(cfg.security.get("ip_deny", [])) if isinstance(cfg.security, dict) else []
        self._admin_token = str((cfg.security or {}).get("admin_token", ""))
        if isinstance(self._admin_token, str) and self._admin_token.startswith("env:"):
            self._admin_token = os.getenv(self._admin_token.split(":",1)[1], "")
        self._require_mtls_for_source = bool((cfg.security or {}).get("require_mtls_for_source", False))
        self._disabled_mountpoints: Set[str] = set((cfg.security or {}).get("disabled_mountpoints", []))
        self._auth_unauth_total: int = 0
        self._auth_unauth_per_mp: Dict[str, int] = {}
        self._source_unauth_total: int = 0
        self._admin_ip_allow = compile_ip_nets((cfg.security or {}).get("admin_ip_allow", [])) if isinstance(cfg.security, dict) else []
        self._admin_rate_limit_per_min = int((cfg.security or {}).get("admin_rate_limit_per_min", 60) or 60)
        self._admin_buckets: Dict[str, TokenBucket] = {}
        self._audit_file = str((cfg.security or {}).get("audit_file", "logs/audit.log"))
        self._audit_max_bytes = int((cfg.security or {}).get("audit_max_bytes", 1048576) or 1048576)
        self._audit_backups = int((cfg.security or {}).get("audit_backups", 3) or 3)
        self._audit_http_url = str((cfg.security or {}).get("audit_http_url", ""))
        self._audit_loki_url = str((cfg.security or {}).get("audit_loki_url", ""))
        self._audit_http_timeout_s = float((cfg.security or {}).get("audit_http_timeout_s", 2.0) or 2.0)
        self._audit_http_headers = dict((cfg.security or {}).get("audit_http_headers", {}) or {})

        self._admin_jwt_secret = str((cfg.security or {}).get("admin_jwt_secret", ""))
        if isinstance(self._admin_jwt_secret, str) and self._admin_jwt_secret.startswith("env:"):
            self._admin_jwt_secret = os.getenv(self._admin_jwt_secret.split(":",1)[1], "")
        self._admin_jwt_exp_s = int((cfg.security or {}).get("admin_jwt_exp_s", 3600) or 3600)
        self._jwt_refresh_exp_s = int((cfg.security or {}).get("admin_jwt_refresh_exp_s", 7*24*3600) or 604800)
        self._jwt_redis_url = str((cfg.security or {}).get("jwt_redis_url", "")) or str((cfg.security or {}).get("shadow_redis_url", ""))
        self._jwt = JwtManager(secret=self._admin_jwt_secret or "", access_exp_s=self._admin_jwt_exp_s, refresh_exp_s=self._jwt_refresh_exp_s, redis_url=self._jwt_redis_url)
        self._admin_lock = asyncio.Lock()
        self._audit: Deque[Dict[str, Any]] = deque(maxlen=1000)
        self._audit_queue: Optional[asyncio.Queue[Dict[str, Any]]] = None
        self._audit_task: Optional[asyncio.Task] = None
        self._cfg_path = cfg_path
        self._cfg_mtime = None
        try:
            if cfg_path and os.path.exists(cfg_path):
                self._cfg_mtime = os.path.getmtime(cfg_path)
        except Exception:
            self._cfg_mtime = None

        # IoT
        self._mqtt = MQTTManager(
            host=str((cfg.security or {}).get("iot_mqtt_host", "")),
            port=int((cfg.security or {}).get("iot_mqtt_port", 1883) or 1883),
            username=str((cfg.security or {}).get("iot_mqtt_username", "")),
            password=str((cfg.security or {}).get("iot_mqtt_password", "")),
            tls=bool((cfg.security or {}).get("iot_mqtt_tls", False)),
        )
        self._proto = ProtobufSerializer()
        self._iot_relays: Dict[str, IoTRelay] = {}
        self._api_task: Optional[asyncio.Task] = None
        self._api_ws_port = int((cfg.security or {}).get("api_ws_port", 0) or 0)

        self._shadow = DeviceShadowStore(url=str((cfg.security or {}).get("shadow_redis_url", "")), ttl_s=int((cfg.security or {}).get("shadow_ttl_s", 86400) or 86400))
        self._geofence_polygons = (cfg.security or {}).get("geofence_polygons", {})

        self._diag_cfg: Dict[str, Any] = dict((cfg.security or {}).get("diagnostics", {}) or {})

        self._events: Deque[Dict[str, Any]] = deque(maxlen=5000)
        self._event_throttle: Dict[str, float] = {}

    def _audit_add(self, *, action: str, client_ip: str, trace_id: str, ok: bool, detail: Optional[Dict[str, Any]] = None) -> None:
        item: Dict[str, Any] = {
            "ts": now_monotonic(),
            "action": action,
            "client_ip": client_ip,
            "trace_id": trace_id,
            "ok": bool(ok),
        }
        if detail:
            item["detail"] = detail
        self._audit.append(item)
        if self._audit_queue is not None:
            try:
                self._audit_queue.put_nowait(item)
            except Exception:
                pass
        try:
            os.makedirs(os.path.dirname(self._audit_file), exist_ok=True)
            line = (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
            with open(self._audit_file, "ab") as f:
                f.write(line)
            if os.path.getsize(self._audit_file) > self._audit_max_bytes:
                if self._audit_backups > 0:
                    oldest = f"{self._audit_file}.{self._audit_backups}"
                    if os.path.exists(oldest):
                        try:
                            os.remove(oldest)
                        except Exception:
                            pass
                    for i in range(self._audit_backups - 1, 0, -1):
                        src = f"{self._audit_file}.{i}"
                        dst = f"{self._audit_file}.{i+1}"
                        if os.path.exists(src):
                            try:
                                os.replace(src, dst)
                            except Exception:
                                pass
                    try:
                        os.replace(self._audit_file, f"{self._audit_file}.1")
                    except Exception:
                        pass
        except Exception:
            pass

    async def _audit_worker(self) -> None:
        assert self._audit_queue is not None
        try:
            while True:
                item = await self._audit_queue.get()
                try:
                    if self._audit_http_url:
                        await asyncio.to_thread(self._audit_send_http, item)
                    if self._audit_loki_url:
                        await asyncio.to_thread(self._audit_send_loki, item)
                except Exception:
                    pass
        except asyncio.CancelledError:
            return

    def _audit_send_http(self, item: Dict[str, Any]) -> None:
        data = json.dumps(item, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self._audit_http_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in (self._audit_http_headers or {}).items():
            req.add_header(str(k), str(v))
        with urllib.request.urlopen(req, timeout=self._audit_http_timeout_s):
            return

    def _audit_send_loki(self, item: Dict[str, Any]) -> None:
        ts_ns = int(item.get("ts", now_monotonic()) * 1_000_000_000)
        line = json.dumps(item, ensure_ascii=False)
        payload = {
            "streams": [
                {
                    "stream": {"app": "babilcors", "type": "audit"},
                    "values": [[str(ts_ns), line]],
                }
            ]
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self._audit_loki_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in (self._audit_http_headers or {}).items():
            req.add_header(str(k), str(v))
        with urllib.request.urlopen(req, timeout=self._audit_http_timeout_s):
            return

    async def _send_json(self, writer: asyncio.StreamWriter, trace_id: str, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        reason = "OK"
        if status >= 400:
            reason = "Error"
        hdr = (
            f"HTTP/1.1 {status} {reason}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + b"traceparent: " + build_traceparent(trace_id, gen_span_id()).encode("ascii") + b"\r\n"
            + b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            + b"Connection: close\r\n\r\n"
        )
        writer.write(hdr)
        writer.write(body)
        await writer.drain()

    async def _send_json_error(self, writer: asyncio.StreamWriter, trace_id: str, status: int, code: str, message: str, details: Optional[Any] = None) -> None:
        payload: Dict[str, Any] = {"code": code, "message": message}
        if details is not None:
            payload["details"] = details
        await self._send_json(writer, trace_id, status, payload)

    def _admin_rate_ok(self, ip: str) -> bool:
        if self._admin_rate_limit_per_min <= 0:
            return True
        b = self._admin_buckets.get(ip)
        rps = max(float(self._admin_rate_limit_per_min) / 60.0, 0.001)
        cap = max(int(self._admin_rate_limit_per_min), 1)
        if b is None:
            b = TokenBucket(rate_bps=rps, capacity=cap)
            self._admin_buckets[ip] = b
        return b.consume(1)

    async def _admin_update_config(self, updater, trace_id: str) -> Tuple[bool, str]:
        if not self._cfg_path:
            return False, "config_path_missing"
        async with self._admin_lock:
            try:
                with open(self._cfg_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                updater(raw)
                directory = os.path.dirname(self._cfg_path) or "."
                fd, tmp_path = tempfile.mkstemp(prefix="caster_cfg_", suffix=".json", dir=directory)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as out:
                        json.dump(raw, out, ensure_ascii=False, indent=2)
                    new_cfg = load_config(tmp_path)
                    validate_config(new_cfg)
                    os.replace(tmp_path, self._cfg_path)
                finally:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                await self.reload_from_disk()
                return True, "ok"
            except PermissionError:
                raise
            except Exception as e:
                return False, str(e)

    async def _kick_rover(self, conn_id: int, mountpoint: str) -> bool:
        hub = self._mountpoints.get(mountpoint)
        if hub is None:
            return False
        async with hub._lock:
            for s in tuple(hub._rovers):
                if s.id == conn_id:
                    try:
                        s.queue.close()
                        if not s.writer.is_closing():
                            s.writer.close()
                    except Exception:
                        pass
                    return True
        return False

    def get_or_create_hub(self, mountpoint: str) -> MountpointHub:
        hub = self._mountpoints.get(mountpoint)
        if hub is None:
            hub = MountpointHub(mountpoint=mountpoint, logger=self._logger)
            self._mountpoints[mountpoint] = hub
            # IoT relay for this mountpoint
            relay = IoTRelay(mqtt=self._mqtt, serializer=self._proto, mountpoint=mountpoint)
            self._iot_relays[mountpoint] = relay
            hub.set_iot_relay(relay)
            asyncio.create_task(relay.start())
        return hub

    def list_mountpoints(self) -> Tuple[str, ...]:
        if self.cfg.sources.mountpoints:
            return tuple(self.cfg.sources.mountpoints)
        return tuple(self._mountpoints.keys())

    async def start(self) -> None:
        ssl_ctx = None
        if self.cfg.listen.tls_certfile and self.cfg.listen.tls_keyfile:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(certfile=self.cfg.listen.tls_certfile, keyfile=self.cfg.listen.tls_keyfile)
            if self.cfg.listen.tls_client_ca:
                try:
                    ssl_ctx.load_verify_locations(self.cfg.listen.tls_client_ca)
                    ssl_ctx.verify_mode = ssl.CERT_OPTIONAL
                    ssl_ctx.check_hostname = False
                except Exception:
                    pass
        self._server = await asyncio.start_server(
            self._handle_conn,
            host=self.cfg.listen.host,
            port=self.cfg.listen.port,
            reuse_port=self.cfg.listen.reuse_port,
            backlog=self.cfg.listen.backlog,
            ssl=ssl_ctx,
        )
        sockets = self._server.sockets or []
        bound = ", ".join(str(s.getsockname()) for s in sockets)
        _log_event(self._logger, logging.INFO, "listening", addr=bound)
        if self._stats_task is None:
            self._stats_task = asyncio.create_task(self._stats_loop())
        # IoT
        try:
            await self._mqtt.start()
        except Exception:
            pass
        try:
            await self._shadow.start()
        except Exception:
            pass
        try:
            await self._jwt.start()
        except Exception:
            pass
        # FastAPI WS
        if self._api_ws_port > 0 and self._api_task is None:
            try:
                import uvicorn
                app = build_app(self._iot_relays, self._shadow, self.get_status_snapshots, self.get_events)
                config = uvicorn.Config(app, host=self.cfg.listen.host, port=int(self._api_ws_port), log_level="warning")
                server = uvicorn.Server(config)
                self._api_task = asyncio.create_task(server.serve())
            except Exception:
                pass

    async def get_status_snapshots(self):
        snaps = []
        for mp, hub in list(self._mountpoints.items()):
            s = await hub.snapshot()
            s["diagnostics_cfg"] = dict(self._diag_cfg)
            snaps.append(s)
        return snaps

    def get_events(self, limit: int = 200):
        lim = max(1, min(int(limit or 200), 2000))
        return list(self._events)[-lim:]

    def _event_add(self, code: str, *, mountpoint: str = "", conn_id: Optional[int] = None, user: str = "", severity: str = "info", msg: str = "", ctx: Optional[Dict[str, Any]] = None, throttle_s: float = 10.0) -> None:
        key = f"{code}:{mountpoint}:{conn_id}:{user}"
        now = now_monotonic()
        last = self._event_throttle.get(key)
        if last is not None and (now - last) < throttle_s:
            return
        self._event_throttle[key] = now
        self._events.append({
            "ts_unix_ms": int(__import__('time').time() * 1000),
            "code": code,
            "severity": severity,
            "message": msg,
            "mountpoint": mountpoint,
            "conn_id": conn_id,
            "user": user,
            "ctx": ctx or {},
        })
        if (self._audit_http_url or self._audit_loki_url) and self._audit_task is None:
            self._audit_queue = asyncio.Queue(maxsize=5000)
            self._audit_task = asyncio.create_task(self._audit_worker())

    def bound_port(self) -> Optional[int]:
        if self._server is None:
            return None
        sockets = self._server.sockets or []
        if not sockets:
            return None
        try:
            return int(sockets[0].getsockname()[1])
        except Exception:
            return None

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._stats_task is not None:
            self._stats_task.cancel()
            try:
                await self._stats_task
            except Exception:
                pass
            self._stats_task = None
        if self._audit_task is not None:
            self._audit_task.cancel()
            try:
                await self._audit_task
            except Exception:
                pass
            self._audit_task = None
            self._audit_queue = None
        hubs = list(self._mountpoints.values())
        for hub in hubs:
            await hub.detach_source()
        # IoT
        for r in list(self._iot_relays.values()):
            try:
                await r.close()
            except Exception:
                pass
        self._iot_relays.clear()
        try:
            await self._mqtt.close()
        except Exception:
            pass
        try:
            await self._shadow.close()
        except Exception:
            pass
        try:
            await self._jwt.close()
        except Exception:
            pass
        if self._api_task is not None:
            try:
                self._api_task.cancel()
                await self._api_task
            except BaseException:
                pass
            self._api_task = None

    def config_changed(self) -> bool:
        if not self._cfg_path:
            return False
        try:
            mt = os.path.getmtime(self._cfg_path)
        except Exception:
            return False
        if self._cfg_mtime is None:
            self._cfg_mtime = mt
            return False
        return mt > self._cfg_mtime

    async def reload_from_disk(self) -> None:
        if not self._cfg_path:
            return
        try:
            new_cfg = load_config(self._cfg_path)
            validate_config(new_cfg)
            self.cfg = new_cfg
            configure_logging_ex(new_cfg.logging)
            self._ip_allow = compile_ip_nets(new_cfg.security.get("ip_allow", [])) if isinstance(new_cfg.security, dict) else []
            self._ip_deny = compile_ip_nets(new_cfg.security.get("ip_deny", [])) if isinstance(new_cfg.security, dict) else []
            self._admin_token = str((new_cfg.security or {}).get("admin_token", ""))
            if isinstance(self._admin_token, str) and self._admin_token.startswith("env:"):
                self._admin_token = os.getenv(self._admin_token.split(":",1)[1], "")
            self._require_mtls_for_source = bool((new_cfg.security or {}).get("require_mtls_for_source", False))
            self._disabled_mountpoints = set((new_cfg.security or {}).get("disabled_mountpoints", []))
            self._api_ws_port = int((new_cfg.security or {}).get("api_ws_port", 0) or 0)
            self._geofence_polygons = (new_cfg.security or {}).get("geofence_polygons", {})
            self._diag_cfg = dict((new_cfg.security or {}).get("diagnostics", {}) or {})
            self._cfg_mtime = os.path.getmtime(self._cfg_path)
            _log_event(self._logger, logging.INFO, "config_reloaded")
        except Exception as e:
            _log_event(self._logger, logging.ERROR, "config_reload_failed", err=str(e))

    async def _stats_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(30.0)
                mps = tuple(self._mountpoints.items())
                for mp, hub in mps:
                    snap = await hub.snapshot()
                    _log_event(
                        self._logger,
                        logging.INFO,
                        "mp_status",
                        mountpoint=mp,
                        source=1 if snap.get("source_attached") else 0,
                        rovers=int(snap.get("rover_count") or 0),
                        rx_bytes_total=int(snap.get("rx_bytes_total") or 0),
                        tx_bytes_total=int(snap.get("tx_bytes_total") or 0),
                        dropped_bytes_total=int(snap.get("dropped_bytes_total") or 0),
                    )
        except asyncio.CancelledError:
            return

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        addr = format_addr(writer.get_extra_info("peername"))
        client_ip = addr.split(":", 1)[0]
        self._conn_seq += 1
        conn_id = self._conn_seq
        trace_id = gen_trace_id()
        _log_event(self._logger, logging.INFO, "connection_open", conn_id=conn_id, client_ip=client_ip, addr=addr, trace_id=trace_id)
        if not ip_allowed(client_ip, self._ip_allow, self._ip_deny):
            _log_event(self._logger, logging.INFO, "ip_forbidden", conn_id=conn_id, client_ip=client_ip)
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            _log_event(self._logger, logging.INFO, "connection_close", conn_id=conn_id, client_ip=client_ip, addr=addr)
            return
        try:
            first = await read_line(reader, limit=8192, timeout_s=5.0)
            if first is None:
                writer.close()
                await writer.wait_closed()
                return
            if first.startswith(b"SOURCE "):
                await self._handle_source(first, reader, writer, addr, conn_id, client_ip, trace_id)
                return
            if first.startswith((b"GET ", b"POST ", b"PATCH ", b"DELETE ", b"PUT ", b"OPTIONS ")):
                await self._handle_http(first, reader, writer, addr, conn_id, client_ip, trace_id)
                return
            _log_event(self._logger, logging.INFO, "unknown_connection", conn_id=conn_id, client_ip=client_ip, addr=addr, trace_id=trace_id)
        except Exception:
            _log_event(self._logger, logging.ERROR, "connection_error", conn_id=conn_id, client_ip=client_ip, addr=addr, trace_id=trace_id)
        finally:
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            _log_event(self._logger, logging.INFO, "connection_close", conn_id=conn_id, client_ip=client_ip, addr=addr, trace_id=trace_id)

    async def _handle_http(self, first: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, addr: str, conn_id: int, client_ip: str, trace_id: str) -> None:
        raw = await read_http_headers(reader, first_line=first)
        if raw is None:
            return
        text = safe_decode(raw)
        lines = text.splitlines()
        if not lines:
            return
        req = lines[0].split()
        if len(req) < 2:
            return
        method = req[0].upper()
        path_raw = req[1]
        path, query = split_path_query(path_raw)
        has_query = "?" in path_raw
        headers: Dict[str, str] = {}
        for ln in lines[1:]:
            if not ln.strip():
                break
            if ":" not in ln:
                continue
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()

        if path == "/healthz":
            await self._send_health(writer, trace_id)
            return

        if path == "/metrics":
            await self._send_metrics_prom(writer, trace_id)
            return

        if path == "/metrics.json":
            await self._send_metrics(writer, trace_id)
            return

        if path.startswith("/admin/"):
            if self._admin_ip_allow and not ip_allowed(client_ip, self._admin_ip_allow, []):
                await self._send_forbidden(writer, trace_id)
                return
            if not self._admin_rate_ok(client_ip):
                await self._send_too_many_requests(writer, trace_id)
                return
            # allow unauthenticated token endpoints
            if path in ("/admin/login", "/admin/token/refresh"):
                authz = ""
                bearer = ""
                token_fp = ""
                claims = {"sub": "public", "role": "admin"}
            else:
                authz = headers.get("authorization", "").strip()
                bearer = authz.split(None,1)[1] if authz.lower().startswith("bearer ") else ""
                token_fp = ""
                claims = None
                if self._admin_jwt_secret and bearer:
                    claims = await self._jwt.decode(bearer, expected_type="access")
                    if claims:
                        token_fp = hashlib.sha256(bearer.encode("utf-8")).hexdigest()[:12]
                if claims is None:
                    if not self._admin_token or bearer != self._admin_token:
                        await self._send_forbidden(writer, trace_id)
                        return
                    claims = {"sub": "static", "role": "admin"}
                    token_fp = hashlib.sha256(bearer.encode("utf-8")).hexdigest()[:12] if bearer else ""
            if has_query and not (path.startswith("/admin/rovers") or path.startswith("/admin/geofences")):
                await self._send_json_error(writer, trace_id, 400, "invalid_request", "query parameters are not allowed for admin api")
                return

            body_json: Dict[str, Any] = {}
            cl = headers.get("content-length")
            ctype = headers.get("content-type", "").lower()
            if method in ("POST", "PATCH") and cl and ctype.startswith("application/json"):
                try:
                    n = int(cl)
                    if n > 0:
                        data = await reader.readexactly(n)
                        body_json = json.loads(safe_decode(data)) if data else {}
                except Exception:
                    body_json = {}

            # RESTful routing under /admin
            parts = path.strip("/").split("/")
            if path == "/admin/login" and method == "POST":
                # body: { username, password }
                uname = str(body_json.get("username") or "")
                pwd = str(body_json.get("password") or "")
                u = self.cfg.users.get(uname)
                if not (self._admin_jwt_secret and u and verify_password(u, pwd)):
                    await self._send_json_error(writer, trace_id, 401, "unauthorized", "invalid creds or jwt secret"); return
                try:
                    role = getattr(u, "role", "admin")
                    if (role or "").lower() not in ("admin", "superadmin", "admin_ro", "admin_readonly", "readonly_admin", "geofence_editor"):
                        await self._send_json_error(writer, trace_id, 403, "forbidden", "role not allowed"); return
                    access, acc_claims = self._jwt.mint_access(sub=uname, role=role)
                    refresh, ref_claims = self._jwt.mint_refresh(sub=uname, role=role)
                except Exception as e:
                    await self._send_json_error(writer, trace_id, 500, "jwt_error", str(e)); return
                await self._send_json(writer, trace_id, 200, {"access": access, "refresh": refresh, "role": role, "exp": acc_claims.get("exp"), "refresh_exp": ref_claims.get("exp")}); return
            if path == "/admin/token/refresh" and method == "POST":
                rtok = str(body_json.get("refresh") or "")
                c = await self._jwt.decode(rtok, expected_type="refresh")
                if not c:
                    await self._send_json_error(writer, trace_id, 401, "unauthorized", "invalid refresh"); return
                sub = str(c.get("sub")); role = str(c.get("role", "admin"))
                # rotate refresh: revoke old refresh jti
                try:
                    await self._jwt.revoke_jti(str(c.get("jti") or ""), int(c.get("exp") or 0))
                except Exception:
                    pass
                access, acc_claims = self._jwt.mint_access(sub=sub, role=role)
                refresh, ref_claims = self._jwt.mint_refresh(sub=sub, role=role)
                await self._send_json(writer, trace_id, 200, {"access": access, "refresh": refresh, "exp": acc_claims.get("exp"), "refresh_exp": ref_claims.get("exp"), "role": role}); return
            if path == "/admin/token/revoke" and method == "POST":
                if not JwtManager.role_allows(str(claims.get("role","admin")), write=True):
                    await self._send_json_error(writer, trace_id, 403, "forbidden", "write requires admin"); return
                tok = str(body_json.get("token") or "")
                jti = str(body_json.get("jti") or "")
                exp = int(body_json.get("exp") or 0)
                if tok and self._admin_jwt_secret:
                    c = await self._jwt.decode(tok, expected_type=str(body_json.get("type") or "access"))
                    if c:
                        jti = str(c.get("jti") or "")
                        exp = int(c.get("exp") or 0)
                if not jti or not exp:
                    await self._send_json_error(writer, trace_id, 400, "invalid_body", "token or jti+exp required"); return
                await self._jwt.revoke_jti(jti, exp)
                await self._send_json(writer, trace_id, 200, {"ok": True}); return
            if path == "/admin/me" and method == "GET":
                await self._send_json(writer, trace_id, 200, {"sub": claims.get("sub"), "role": claims.get("role")}); return
            if len(parts) >= 2 and parts[1] in ("users", "tiers", "mountpoints", "geofences"):
                resource = parts[1]
                ident = parts[2] if len(parts) >= 3 else None
                sub = parts[3] if len(parts) >= 4 else None
                role = str(claims.get("role", "admin"))
                sub_user = str(claims.get("sub", ""))
                write = method in ("POST", "PATCH", "DELETE")
                if resource != "geofences" and write and not JwtManager.role_allows(role, write=True):
                    await self._send_json_error(writer, trace_id, 403, "forbidden", "write requires admin")
                    return

                # Users
                if resource == "users":
                    if method == "GET" and ident is None:
                        await self._send_json(writer, trace_id, 200, {"users": list(self.cfg.users.keys())}); return
                    if method == "GET" and ident:
                        u = self.cfg.users.get(ident)
                        if not u:
                            await self._send_json_error(writer, trace_id, 404, "not_found", "user not found"); return
                        await self._send_json(writer, trace_id, 200, {"username": u.username, "tier": u.tier, "mountpoints": list(u.mountpoints), "geofence_id": getattr(u, "geofence_id", None), "role": getattr(u, "role", "user")}); return
                    if method == "POST" and ident is None:
                        schema = {"type": "object", "properties": {"username": {"type": "string", "minLength": 1}, "tier": {"type": "string"}, "mountpoints": {"type": "array", "items": {"type": "string"}}, "password": {"type": "string"}, "password_sha256": {"type": "string"}, "password_hash": {"type": "string"}}, "required": ["username", "tier", "mountpoints"], "additionalProperties": True}
                        try:
                            js_validate(instance=body_json, schema=schema)  # type: ignore
                        except JSValidationError as e:
                            await self._send_json_error(writer, trace_id, 400, "invalid_body", "validation error", str(e)); return
                        uname = str(body_json.get("username"))
                        def upd(raw):
                            users = raw.setdefault("users", {})
                            if uname in users:
                                raise ValueError("user exists")
                            users[uname] = {k: body_json[k] for k in ("password", "password_sha256", "password_hash", "tier", "mountpoints", "geofence_id", "role") if k in body_json}
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 409, "conflict", str(e)); return
                        await self._send_json(writer, trace_id, 201 if ok else 400, {"ok": ok, "msg": msg}); return
                    if method == "PATCH" and ident:
                        schema = {"type": "object", "properties": {"tier": {"type": "string"}, "mountpoints": {"type": "array", "items": {"type": "string"}}, "password": {"type": "string"}, "password_sha256": {"type": "string"}, "password_hash": {"type": "string"}}, "additionalProperties": True}
                        try:
                            js_validate(instance=body_json, schema=schema)  # type: ignore
                        except JSValidationError as e:
                            await self._send_json_error(writer, trace_id, 400, "invalid_body", "validation error", str(e)); return
                        def upd(raw):
                            u = (raw.get("users") or {}).get(ident)
                            if not u:
                                raise ValueError("not found")
                            for k in ("tier", "password", "password_sha256", "password_hash"):
                                if k in body_json:
                                    u[k] = body_json[k]
                            if "mountpoints" in body_json:
                                u["mountpoints"] = body_json.get("mountpoints")
                            if "geofence_id" in body_json:
                                u["geofence_id"] = body_json.get("geofence_id")
                            if "role" in body_json:
                                u["role"] = body_json.get("role")
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 404, "not_found", str(e)); return
                        await self._send_json(writer, trace_id, 200 if ok else 400, {"ok": ok, "msg": msg}); return
                    if method == "DELETE" and ident:
                        def upd(raw):
                            users = raw.get("users") or {}
                            if ident in users:
                                del users[ident]
                            else:
                                raise ValueError("not found")
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 404, "not_found", str(e)); return
                        await self._send_json(writer, trace_id, 204, {"ok": True}); return

                # Tiers
                if resource == "tiers":
                    if method == "GET" and ident is None:
                        await self._send_json(writer, trace_id, 200, {"tiers": list(self.cfg.tiers.keys())}); return
                    if method == "GET" and ident:
                        t = self.cfg.tiers.get(ident)
                        if not t:
                            await self._send_json_error(writer, trace_id, 404, "not_found", "tier not found"); return
                        await self._send_json(writer, trace_id, 200, {"name": ident, "rate_limit_bps": t.rate_limit_bps, "max_epochs_per_minute": t.max_epochs_per_minute, "max_queue_bytes": t.max_queue_bytes}); return
                    if method == "POST" and ident is None:
                        schema = {"type": "object", "properties": {"name": {"type": "string", "minLength": 1}, "rate_limit_bps": {"type": "integer", "minimum": 0}, "max_epochs_per_minute": {"type": "integer", "minimum": 0}, "max_queue_bytes": {"type": "integer", "minimum": 0}}, "required": ["name"], "additionalProperties": False}
                        try:
                            js_validate(instance=body_json, schema=schema)  # type: ignore
                        except JSValidationError as e:
                            await self._send_json_error(writer, trace_id, 400, "invalid_body", "validation error", str(e)); return
                        name = str(body_json["name"])            
                        def upd(raw):
                            tiers = raw.setdefault("tiers", {})
                            if name in tiers:
                                raise ValueError("tier exists")
                            tiers[name] = {k: int(body_json.get(k, 0) or 0) for k in ("rate_limit_bps", "max_epochs_per_minute", "max_queue_bytes")}
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 409, "conflict", str(e)); return
                        await self._send_json(writer, trace_id, 201 if ok else 400, {"ok": ok, "msg": msg}); return
                    if method == "PATCH" and ident:
                        schema = {"type": "object", "properties": {"rate_limit_bps": {"type": "integer", "minimum": 0}, "max_epochs_per_minute": {"type": "integer", "minimum": 0}, "max_queue_bytes": {"type": "integer", "minimum": 0}}, "additionalProperties": False}
                        try:
                            js_validate(instance=body_json, schema=schema)  # type: ignore
                        except JSValidationError as e:
                            await self._send_json_error(writer, trace_id, 400, "invalid_body", "validation error", str(e)); return
                        def upd(raw):
                            t = (raw.get("tiers") or {}).get(ident)
                            if not t:
                                raise ValueError("not found")
                            for k, v in body_json.items():
                                t[k] = int(v)
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 404, "not_found", str(e)); return
                        await self._send_json(writer, trace_id, 200 if ok else 400, {"ok": ok, "msg": msg}); return
                    if method == "DELETE" and ident:
                        def upd(raw):
                            tiers = raw.get("tiers") or {}
                            if ident in tiers:
                                del tiers[ident]
                            else:
                                raise ValueError("not found")
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 404, "not_found", str(e)); return
                        await self._send_json(writer, trace_id, 204, {"ok": True}); return

                # Mountpoints
                if resource == "mountpoints":
                    if method == "GET" and ident is None:
                        await self._send_json(writer, trace_id, 200, {"mountpoints": list(self.cfg.sources.mountpoints)}); return
                    if method == "POST" and ident is None:
                        schema = {"type": "object", "properties": {"name": {"type": "string", "minLength": 1}}, "required": ["name"], "additionalProperties": False}
                        try:
                            js_validate(instance=body_json, schema=schema)  # type: ignore
                        except JSValidationError as e:
                            await self._send_json_error(writer, trace_id, 400, "invalid_body", "validation error", str(e)); return
                        name = str(body_json["name"])            
                        def upd(raw):
                            mps = list(raw.get("sources", {}).get("mountpoints", []))
                            if name in mps:
                                raise ValueError("exists")
                            mps.append(name)
                            raw.setdefault("sources", {})["mountpoints"] = mps
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 409, "conflict", str(e)); return
                        await self._send_json(writer, trace_id, 201 if ok else 400, {"ok": ok, "msg": msg}); return
                    if method == "DELETE" and ident:
                        def upd(raw):
                            mps = list(raw.get("sources", {}).get("mountpoints", []))
                            if ident in mps:
                                mps.remove(ident)
                                raw.setdefault("sources", {})["mountpoints"] = mps
                            else:
                                raise ValueError("not found")
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 404, "not_found", str(e)); return
                        await self._send_json(writer, trace_id, 204, {"ok": True}); return
                    if method in ("PATCH", "POST") and ident and sub == "meta":
                        # Merge meta fields
                        def upd(raw):
                            st = raw.setdefault("sourcetable", {})
                            mm = st.setdefault("mountpoints_meta", {})
                            item = mm.setdefault(ident, {})
                            for k, v in (body_json or {}).items():
                                if k in {"latitude", "longitude"}:
                                    item[k] = float(v)
                                elif k in {"bitrate", "carrier"}:
                                    item[k] = int(v)
                                else:
                                    item[k] = v
                        ok, msg = await self._admin_update_config(upd, trace_id)
                        await self._send_json(writer, trace_id, 200 if ok else 400, {"ok": ok, "msg": msg}); return
                if resource == "geofences":
                    # Authorization:
                    # - admin: full access
                    # - geofence_editor: can create/update/delete only own geofences
                    is_admin = JwtManager.role_allows(role, write=True)
                    is_editor = (role or "").lower() == "geofence_editor"
                    if write and not (is_admin or is_editor):
                        await self._send_json_error(writer, trace_id, 403, "forbidden", "write requires admin or geofence_editor")
                        return
                    if is_editor and sub_user in ("", "public", "static"):
                        await self._send_json_error(writer, trace_id, 403, "forbidden", "geofence_editor requires jwt subject")
                        return
                    if method == "GET" and ident is None:
                        # optional bbox filter via query param bbox=minLon,minLat,maxLon,maxLat
                        qb = None
                        try:
                            bbox_str = query.get("bbox") if isinstance(query, dict) else None
                            if bbox_str:
                                parts_b = [p.strip() for p in bbox_str.split(",")]
                                if len(parts_b) == 4:
                                    qb = (float(parts_b[0]), float(parts_b[1]), float(parts_b[2]), float(parts_b[3]))
                        except Exception:
                            qb = None
                        out = self._geofence_polygons or {}
                        if is_editor and not is_admin:
                            out = {gid: g for gid, g in out.items() if str((g or {}).get("owner") or "") == sub_user}
                        if qb:
                            flt = {}
                            for gid, g in out.items():
                                b = g.get("bbox")
                                if b and isinstance(b, (list, tuple)) and len(b) == 4:
                                    try:
                                        bb = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                                    except Exception:
                                        bb = None
                                else:
                                    bb = None
                                if bb is None:
                                    poly = g.get("polygon") or []
                                    if poly:
                                        bb = polygon_bbox([(float(a), float(b)) for a, b in poly])
                                if bb and bbox_intersects(bb, qb):
                                    flt[gid] = g
                            out = flt
                        # optional format=geojson
                        if (query.get("format") if isinstance(query, dict) else "") == "geojson":
                            feats = []
                            for gid, g in out.items():
                                gj = g.get("geojson")
                                if not isinstance(gj, dict):
                                    poly = g.get("polygon") or []
                                    if poly:
                                        gj = rings_to_geojson_polygon([(float(a), float(b)) for a, b in poly])
                                feats.append({"type": "Feature", "properties": {"id": gid, "mode": g.get("mode")}, "geometry": gj})
                            await self._send_json(writer, trace_id, 200, {"type": "FeatureCollection", "features": feats}); return
                        await self._send_json(writer, trace_id, 200, {"geofences": out}); return
                    if method == "POST" and ident is None:
                        # body: { id, polygon: [[lat,lon],...], mode: "alert"|"block" }
                        gid = str((body_json.get("id")) or "")
                        poly = body_json.get("polygon") or []
                        mode = (body_json.get("mode") or "alert").lower()
                        if not gid:
                            await self._send_json_error(writer, trace_id, 400, "invalid_body", "id required"); return
                        gj = body_json.get("geojson") if isinstance(body_json.get("geojson"), dict) else None
                        if (not poly) and gj:
                            rings = geojson_to_rings(gj)
                            if rings:
                                poly = [[float(a), float(b)] for a, b in rings[0]]
                        if not isinstance(poly, list) or not poly:
                            await self._send_json_error(writer, trace_id, 400, "invalid_body", "polygon or geojson required"); return
                        bbox = None
                        try:
                            bbox = polygon_bbox([(float(a), float(b)) for a, b in poly])
                        except Exception:
                            bbox = None
                        def upd(raw):
                            sec = raw.setdefault("security", {})
                            gf = sec.setdefault("geofence_polygons", {})
                            rec = {"polygon": poly, "mode": mode}
                            if bbox:
                                rec["bbox"] = list(bbox)
                            if gj:
                                rec["geojson"] = gj
                            if is_admin and body_json.get("owner"):
                                rec["owner"] = str(body_json.get("owner"))
                            elif is_editor:
                                rec["owner"] = sub_user
                            gf[gid] = rec
                        ok, msg = await self._admin_update_config(upd, trace_id)
                        await self._send_json(writer, trace_id, 201 if ok else 400, {"ok": ok, "msg": msg}); return
                    if method == "PATCH" and ident:
                        poly = body_json.get("polygon")
                        mode = body_json.get("mode")
                        gj = body_json.get("geojson") if isinstance(body_json.get("geojson"), dict) else None
                        if poly is None and mode is None:
                            await self._send_json_error(writer, trace_id, 400, "invalid_body", "polygon or mode required"); return
                        def upd(raw):
                            sec = raw.setdefault("security", {})
                            gf = sec.setdefault("geofence_polygons", {})
                            cur = gf.get(ident)
                            if not cur:
                                raise ValueError("not found")
                            if is_editor and not is_admin and str(cur.get("owner") or "") != sub_user:
                                raise PermissionError("not owner")
                            if gj is not None:
                                rings = geojson_to_rings(gj)
                                if rings:
                                    cur["polygon"] = [[float(a), float(b)] for a, b in rings[0]]
                                    cur["geojson"] = gj
                            if poly is not None:
                                cur["polygon"] = poly
                            if mode is not None:
                                cur["mode"] = str(mode)
                            if is_admin and "owner" in body_json:
                                cur["owner"] = str(body_json.get("owner") or "")
                            try:
                                b2 = polygon_bbox([(float(a), float(b)) for a, b in (cur.get("polygon") or [])])
                                cur["bbox"] = list(b2)
                            except Exception:
                                pass
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except PermissionError as e:
                            await self._send_json_error(writer, trace_id, 403, "forbidden", str(e)); return
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 404, "not_found", str(e)); return
                        await self._send_json(writer, trace_id, 200 if ok else 400, {"ok": ok, "msg": msg}); return
                    if method == "DELETE" and ident:
                        def upd(raw):
                            sec = raw.setdefault("security", {})
                            gf = sec.setdefault("geofence_polygons", {})
                            cur = gf.get(ident)
                            if cur is None:
                                raise ValueError("not found")
                            if is_editor and not is_admin and str(cur.get("owner") or "") != sub_user:
                                raise PermissionError("not owner")
                            del gf[ident]
                        try:
                            ok, msg = await self._admin_update_config(upd, trace_id)
                        except PermissionError as e:
                            await self._send_json_error(writer, trace_id, 403, "forbidden", str(e)); return
                        except Exception as e:
                            await self._send_json_error(writer, trace_id, 404, "not_found", str(e)); return
                        if not ok:
                            await self._send_json_error(writer, trace_id, 400, "update_failed", msg); return
                        await self._send_json(writer, trace_id, 204, {"ok": True}); return

            # Operational endpoints are write operations
            if path in ("/admin/disable", "/admin/enable", "/admin/kick", "/admin/audit") and method == "POST":
                if not JwtManager.role_allows(str(claims.get("role", "admin")), write=True):
                    await self._send_json_error(writer, trace_id, 403, "forbidden", "write requires admin"); return
            if path == "/admin/openapi.json":
                spec = {
                    "openapi": "3.0.0",
                    "info": {"title": "BabilCORS Admin API", "version": "1.0.0"},
                    "paths": {
                        "/admin/status": {"get": {"summary": "Status"}},
                        "/admin/me": {"get": {"summary": "Who am I"}},
                        "/admin/login": {"post": {"summary": "Login (JWT)"}},
                        "/admin/token/refresh": {"post": {"summary": "Refresh token"}},
                        "/admin/token/revoke": {"post": {"summary": "Revoke token"}},
                        "/admin/users": {"post": {"summary": "Users CRUD"}},
                        "/admin/tiers": {"post": {"summary": "Tiers CRUD"}},
                        "/admin/mountpoints": {"post": {"summary": "Mountpoints CRUD"}},
                        "/admin/geofences": {"post": {"summary": "Geofences CRUD"}},
                        "/admin/rovers": {"get": {"summary": "Rovers (paged)"}},
                        "/admin/disable": {"post": {"summary": "Disable mountpoint"}},
                        "/admin/enable": {"post": {"summary": "Enable mountpoint"}},
                        "/admin/kick": {"post": {"summary": "Kick rover"}},
                        "/admin/audit": {"post": {"summary": "Audit tail"}}
                    }
                }
                await self._send_json(writer, trace_id, 200, spec); return
            if path == "/admin/status":
                snaps = []
                for mp, hub in list(self._mountpoints.items()):
                    snaps.append(await hub.snapshot())
                await self._send_json(writer, trace_id, 200, {"mountpoints": snaps, "disabled": sorted(self._disabled_mountpoints)})
                return
            if path.startswith("/admin/rovers"):
                try:
                    page = int((query.get("page") if isinstance(query, dict) else "") or "1")
                except Exception:
                    page = 1
                try:
                    limit = int((query.get("limit") if isinstance(query, dict) else "") or "50")
                except Exception:
                    limit = 50
                mp_filter = (query.get("mountpoint") if isinstance(query, dict) else None)
                page = max(page, 1)
                limit = max(min(limit, 500), 1)
                items = []
                total = 0
                for mp, hub in list(self._mountpoints.items()):
                    if mp_filter and mp != mp_filter:
                        continue
                    async with hub._lock:
                        for s in hub._rovers:
                            total += 1
                            items.append(
                                {
                                    "mountpoint": mp,
                                    "conn_id": s.id,
                                    "user": s.username,
                                    "client_ip": (s.addr.split(":", 1)[0] if s.addr else ""),
                                    "sent_bytes": s.sent_bytes,
                                    "dropped_bytes": s.dropped_bytes,
                                    "last_nmea_age_s": (now_monotonic() - getattr(s, "_last_nmea_ts_mono", 0.0)) if getattr(s, "_last_nmea_ts_mono", 0.0) else None,
                                    "gsv_snr_mean": s.last_gsv_snr_mean,
                                    "gsv_total_sv": s.last_gsv_total_sv,
                                }
                            )
                start = (page - 1) * limit
                end = start + limit
                await self._send_json(writer, trace_id, 200, {"page": page, "limit": limit, "total": total, "items": items[start:end]})
                return
            if path.startswith("/admin/disable"):
                if method != "POST":
                    await self._send_json_error(writer, trace_id, 405, "method_not_allowed", "use POST")
                    return
                mp = str(body_json.get("mountpoint", ""))
                if mp:
                    def upd(raw):
                        sec = raw.setdefault("security", {})
                        cur = list(sec.get("disabled_mountpoints", []) or [])
                        if mp not in cur:
                            cur.append(mp)
                        sec["disabled_mountpoints"] = cur
                    ok, msg = await self._admin_update_config(upd, trace_id)
                    self._audit_add(action="disable", client_ip=client_ip, trace_id=trace_id, ok=ok, detail={"mountpoint": mp, "msg": msg, "token_fp": token_fp})
                    await self._send_json(writer, trace_id, 200 if ok else 400, {"ok": ok, "msg": msg})
                    return
                await self._send_json(writer, trace_id, 400, {"ok": False, "msg": "mountpoint_required"}); return
            if path.startswith("/admin/enable"):
                if method != "POST":
                    await self._send_json_error(writer, trace_id, 405, "method_not_allowed", "use POST")
                    return
                mp = str(body_json.get("mountpoint", ""))
                if mp:
                    def upd(raw):
                        sec = raw.setdefault("security", {})
                        cur = list(sec.get("disabled_mountpoints", []) or [])
                        if mp in cur:
                            cur.remove(mp)
                        sec["disabled_mountpoints"] = cur
                    ok, msg = await self._admin_update_config(upd, trace_id)
                    self._audit_add(action="enable", client_ip=client_ip, trace_id=trace_id, ok=ok, detail={"mountpoint": mp, "msg": msg, "token_fp": token_fp})
                    await self._send_json(writer, trace_id, 200 if ok else 400, {"ok": ok, "msg": msg})
                    return
                await self._send_json(writer, trace_id, 400, {"ok": False, "msg": "mountpoint_required"}); return
            if path.startswith("/admin/kick"):
                if method != "POST":
                    await self._send_json_error(writer, trace_id, 405, "method_not_allowed", "use POST")
                    return
                connv = body_json.get("conn_id")
                mpv = body_json.get("mountpoint")
                ok = False
                try:
                    if connv and mpv:
                        ok = await self._kick_rover(int(connv), mpv)
                except Exception:
                    ok = False
                self._audit_add(action="kick", client_ip=client_ip, trace_id=trace_id, ok=ok, detail={"mountpoint": mpv, "conn_id": connv, "token_fp": token_fp})
                await self._send_json(writer, trace_id, 200 if ok else 404, {"ok": ok})
                return

            if path.startswith("/admin/audit"):
                if method != "POST":
                    await self._send_json_error(writer, trace_id, 405, "method_not_allowed", "use POST")
                    return
                try:
                    limit = int(body_json.get("limit", 100) or 100)
                except Exception:
                    limit = 100
                items = list(self._audit)[-max(1, min(limit, 1000)) :]
                await self._send_json(writer, trace_id, 200, {"items": items})
                return

            # Legacy action/query endpoints removed

        if path == "/" or path == "":
            await self._send_sourcetable(writer, trace_id)
            return

        mountpoint = path.lstrip("/")
        if mountpoint in self._disabled_mountpoints:
            await self._send_forbidden(writer, trace_id)
            return
        auth = parse_basic_auth(headers.get("authorization", ""))
        if auth is None:
            self._auth_unauth_total += 1
            self._auth_unauth_per_mp[mountpoint] = self._auth_unauth_per_mp.get(mountpoint,0)+1
            _log_event(self._logger, logging.INFO, "auth_unauthorized", conn_id=conn_id, client_ip=client_ip, mountpoint=mountpoint, trace_id=trace_id)
            await self._send_unauthorized(writer, trace_id)
            return
        username, password = auth
        user = self.cfg.users.get(username)
        if user is None or not verify_password(user, password):
            self._auth_unauth_total += 1
            self._auth_unauth_per_mp[mountpoint] = self._auth_unauth_per_mp.get(mountpoint,0)+1
            _log_event(self._logger, logging.INFO, "auth_unauthorized", conn_id=conn_id, client_ip=client_ip, user=username, mountpoint=mountpoint, trace_id=trace_id)
            await self._send_unauthorized(writer, trace_id)
            return
        if not user_can_access(user, mountpoint):
            _log_event(self._logger, logging.INFO, "auth_forbidden", conn_id=conn_id, client_ip=client_ip, user=username, mountpoint=mountpoint, trace_id=trace_id)
            await self._send_forbidden(writer, trace_id)
            return

        tier = self.cfg.tiers.get(user.tier) or Tier(rate_limit_bps=0, max_epochs_per_minute=0, max_queue_bytes=262144)
        ip = client_ip
        max_total = int(self.cfg.limits.get("max_rovers_total", 0) or 0)
        max_per_ip = int(self.cfg.limits.get("max_rovers_per_ip", 0) or 0)
        if max_total > 0 and self._rover_total >= max_total:
            _log_event(self._logger, logging.INFO, "rover_rejected_limit", conn_id=conn_id, client_ip=client_ip, user=username, mountpoint=mountpoint)
            await self._send_forbidden(writer, trace_id)
            return
        if max_per_ip > 0 and self._rover_ip_counts.get(ip, 0) >= max_per_ip:
            _log_event(self._logger, logging.INFO, "rover_rejected_limit", conn_id=conn_id, client_ip=client_ip, user=username, mountpoint=mountpoint)
            await self._send_forbidden(writer, trace_id)
            return
        self._id_seq += 1
        bucket = None
        if tier.rate_limit_bps > 0:
            bucket = TokenBucket(rate_bps=tier.rate_limit_bps, capacity=max(tier.rate_limit_bps * 2, 1))
        session = RoverSession(
            id=self._id_seq,
            username=username,
            tier=tier,
            mountpoint=mountpoint,
            addr=addr,
            reader=reader,
            writer=writer,
            queue=ByteQueue(tier.max_queue_bytes or 262144),
            bucket=bucket,
        )

        gga_header = headers.get("ntrip-gga")
        if gga_header:
            gga = parse_gga(gga_header.strip())
            if gga:
                session.last_gga = gga
            _log_event(
                self._logger,
                logging.INFO,
                "rover_gga",
                user=username,
                    client_ip=client_ip,
                    mountpoint=mountpoint,
                lat=gga[0],
                lon=gga[1],
                fixq=gga[2],
                nsat=gga[3],
                hdop=gga[4],
            )

        hub = self.get_or_create_hub(mountpoint)
        await hub.add_rover(session)
        self._rover_total += 1
        self._rover_ip_counts[ip] = self._rover_ip_counts.get(ip, 0) + 1
        _log_event(self._logger, logging.INFO, "rover_accepted", conn_id=conn_id, client_ip=client_ip, user=username, mountpoint=mountpoint)
        await self._send_icy_ok(writer, trace_id)
        send_task = asyncio.create_task(self._rover_send_loop(hub, session))
        read_task = asyncio.create_task(self._rover_read_loop(hub, session))
        done, pending = await asyncio.wait({send_task, read_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        session.queue.close()
        await hub.remove_rover(session)
        self._rover_total = max(self._rover_total - 1, 0)
        self._rover_ip_counts[ip] = max(self._rover_ip_counts.get(ip, 1) - 1, 0)
        for t in done:
            _ = t.exception() if t.cancelled() is False else None

    async def _handle_source(self, first: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, addr: str, conn_id: int, client_ip: str, trace_id: str) -> None:
        line = safe_decode(first).strip()
        parts = line.split()
        if len(parts) < 3:
            return
        password = parts[1]
        mountpoint = parts[2].lstrip("/")
        if password != self.cfg.sources.password:
            self._source_unauth_total += 1
            _log_event(self._logger, logging.INFO, "source_unauthorized", conn_id=conn_id, client_ip=client_ip, mountpoint=mountpoint, trace_id=trace_id)
            await self._send_source_forbidden(writer)
            return
        if self.cfg.sources.mountpoints and mountpoint not in self.cfg.sources.mountpoints:
            _log_event(self._logger, logging.INFO, "source_forbidden", conn_id=conn_id, client_ip=client_ip, mountpoint=mountpoint, trace_id=trace_id)
            await self._send_source_forbidden(writer)
            return

        hub = self.get_or_create_hub(mountpoint)
        if self._require_mtls_for_source and self.cfg.listen.tls_certfile:
            try:
                ssl_obj = writer.get_extra_info("ssl_object")
                has_cert = bool(ssl_obj and ssl_obj.getpeercert())
            except Exception:
                has_cert = False
            if not has_cert:
                writer.write(b"ERROR - Client Cert Required\r\n"); await writer.drain(); return
        max_sources = int(self.cfg.limits.get("max_sources_total", 0) or 0)
        if max_sources > 0 and hub.source_addr is None:
            active = sum(1 for h in self._mountpoints.values() if h.source_addr is not None)
            if active >= max_sources:
                _log_event(self._logger, logging.INFO, "source_rejected_limit", conn_id=conn_id, client_ip=client_ip, mountpoint=mountpoint, trace_id=trace_id)
                writer.write(b"ERROR - Too Many Sources\r\n")
                await writer.drain()
                return
        ok = await hub.attach_source(addr)
        if not ok:
            _log_event(self._logger, logging.INFO, "source_rejected_busy", conn_id=conn_id, client_ip=client_ip, mountpoint=mountpoint, trace_id=trace_id)
            writer.write(b"ERROR - Mountpoint Busy\r\n")
            await writer.drain()
            return
        await self._send_icy_ok(writer, trace_id)
        await writer.drain()

        try:
            idle_t = int(self.cfg.limits.get("source_idle_timeout_s", 0) or 0)
            parser = RtcmStreamParser()
            while True:
                if idle_t > 0:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=idle_t)
                else:
                    chunk = await reader.read(4096)
                if not chunk:
                    break
                frames, errs = parser.feed(chunk)
                if errs:
                    hub.record_rtcm_crc_errors(errs)
                if frames:
                    out = bytearray()
                    for fr in frames:
                        hub.record_rtcm_frame(fr.msg_type)
                        # station info extraction
                        payload = fr.raw[3:-3]
                        if fr.msg_type == 1033:
                            info = parse_rtcm_1033(payload)
                            if info:
                                hub.record_station_info(info)
                        elif fr.msg_type in (1005, 1006):
                            info = parse_rtcm_1005_1006(payload)
                            if info:
                                hub.record_station_info(info)
                        elif fr.msg_type in (1007, 1008):
                            info = parse_rtcm_1007_1008(payload)
                            if info:
                                hub.record_station_info(info)
                        hub.publish_rtcm_frame(frame=fr.raw, msg_type=fr.msg_type)
                        out.extend(fr.raw)
                    await hub.on_source_data(bytes(out))
        finally:
            await hub.detach_source()

    async def _rover_read_loop(self, hub: MountpointHub, session: RoverSession) -> None:
        while True:
            line_b = await read_line(session.reader, limit=1024, timeout_s=60.0)
            if line_b is None:
                return
            line = safe_decode(line_b).strip()
            if not line.startswith("$"):
                continue
            # RMC
            if "RMC" in line:
                rmc = parse_rmc(line)
                if rmc:
                    session._last_nmea_ts_mono = now_monotonic()
                    session.last_rmc = rmc
                    _log_event(
                        self._logger,
                        logging.INFO,
                        "rover_rmc",
                        conn_id=session.id,
                        client_ip=session.addr.split(":", 1)[0],
                        user=session.username,
                        mountpoint=session.mountpoint,
                        status=rmc[0],
                        lat=rmc[1],
                        lon=rmc[2],
                        speed_knots=rmc[3],
                        course=rmc[4],
                    )
                continue
            # VTG
            if "VTG" in line:
                vtg = parse_vtg(line)
                if vtg:
                    session._last_nmea_ts_mono = now_monotonic()
                    session.last_vtg = vtg
                    _log_event(
                        self._logger,
                        logging.INFO,
                        "rover_vtg",
                        conn_id=session.id,
                        client_ip=session.addr.split(":", 1)[0],
                        user=session.username,
                        mountpoint=session.mountpoint,
                        course=vtg[0],
                        speed_kmh=vtg[1],
                    )
                continue
            # ZDA
            if "ZDA" in line:
                zda = parse_zda(line)
                if zda:
                    session._last_nmea_ts_mono = now_monotonic()
                    session.last_zda = zda
                    _log_event(
                        self._logger,
                        logging.INFO,
                        "rover_zda",
                        conn_id=session.id,
                        client_ip=session.addr.split(":", 1)[0],
                        user=session.username,
                        mountpoint=session.mountpoint,
                    )
                continue
            if "GGA" in line:
                gga = parse_gga(line)
                if not gga:
                    continue
                session._last_nmea_ts_mono = now_monotonic()
                session.last_gga = gga
                if gga[2] == 0:
                    self._event_add("NO_FIX", mountpoint=session.mountpoint, conn_id=session.id, user=session.username, severity="warning", msg="Fix yok", ctx={"nsat": gga[3], "hdop": gga[4]})

                # spoofing heuristic: sudden large jump
                try:
                    lat, lon = float(gga[0]), float(gga[1])
                    if session._last_lat is not None and session._last_lon is not None and session._last_pos_ts_mono:
                        dt = max(now_monotonic() - session._last_pos_ts_mono, 0.001)
                        dist = haversine_m(session._last_lat, session._last_lon, lat, lon)
                        speed = dist / dt
                        dist_thr = float(self._diag_cfg.get("spoofing_jump_dist_m", 500.0) or 500.0)
                        spd_thr = float(self._diag_cfg.get("spoofing_jump_speed_mps", 80.0) or 80.0)
                        if dist > dist_thr and speed > spd_thr:
                            session._spoofing_suspect_ts_mono = now_monotonic()
                            self._event_add(
                                "SPOOFING_SUSPECT",
                                mountpoint=session.mountpoint,
                                conn_id=session.id,
                                user=session.username,
                                severity="warning",
                                msg="Olası spoofing (ani konum sıçraması)",
                                ctx={"dist_m": round(dist, 1), "speed_mps": round(speed, 1), "dist_thr": dist_thr, "speed_thr": spd_thr},
                                throttle_s=30.0,
                            )
                    session._last_lat = lat
                    session._last_lon = lon
                    session._last_pos_ts_mono = now_monotonic()
                except Exception:
                    pass

                # Shadow update
                try:
                    device_id = session.username or f"{session.mountpoint}:{session.id}"
                    await self._shadow.upsert(device_id, {
                        "mountpoint": session.mountpoint,
                        "username": session.username,
                        "lat": gga[0],
                        "lon": gga[1],
                        "fixq": gga[2],
                        "nsat": gga[3],
                        "hdop": gga[4],
                    })
                except Exception:
                    pass
                # Geofence check
                try:
                    # Prefer user-specific geofence
                    urec = self.cfg.users.get(session.username)
                    gf = None
                    if urec and getattr(urec, "geofence_id", None):
                        gf = (self._geofence_polygons or {}).get(getattr(urec, "geofence_id"))
                    if gf is None:
                        gf = (self._geofence_polygons or {}).get(session.mountpoint) or (self._geofence_polygons or {}).get("*")
                    if isinstance(gf, dict):
                        poly = gf.get("polygon") or []
                        mode = (gf.get("mode") or "alert").lower()
                        if poly:
                            inside = point_in_polygon((gga[0], gga[1]), [(float(a), float(b)) for a, b in poly])
                            if not inside:
                                session._geofence_violation_ts_mono = now_monotonic()
                                self._event_add(
                                    "GEOFENCE_VIOLATION",
                                    mountpoint=session.mountpoint,
                                    conn_id=session.id,
                                    user=session.username,
                                    severity="warning" if mode == "block" else "info",
                                    msg="Geofence ihlali",
                                    ctx={"mode": mode, "lat": gga[0], "lon": gga[1]},
                                    throttle_s=10.0,
                                )
                                _log_event(self._logger, logging.WARN if hasattr(logging, 'WARN') else logging.WARNING, "geofence_violation", conn_id=session.id, user=session.username, mountpoint=session.mountpoint)
                                if mode == "block":
                                    try:
                                        if not session.writer.is_closing():
                                            session.writer.close()
                                    except Exception:
                                        pass
                                    return
                except Exception:
                    pass
                _log_event(
                    self._logger,
                    logging.INFO,
                    "rover_gga",
                    conn_id=session.id,
                    client_ip=session.addr.split(":", 1)[0],
                    user=session.username,
                    mountpoint=session.mountpoint,
                    lat=gga[0],
                    lon=gga[1],
                    fixq=gga[2],
                    nsat=gga[3],
                    hdop=gga[4],
                )
                continue
            if "GSA" in line:
                gsa = parse_gsa(line)
                if gsa:
                    session._last_nmea_ts_mono = now_monotonic()
                    session.last_gsa = gsa
                    try:
                        device_id = session.username or f"{session.mountpoint}:{session.id}"
                        await self._shadow.upsert(device_id, {
                            "mountpoint": session.mountpoint,
                            "username": session.username,
                            "fix_type": gsa[1],
                            "pdop": gsa[3],
                            "hdop": gsa[4],
                            "vdop": gsa[5],
                            "used": len(gsa[2]),
                        })
                    except Exception:
                        pass
                    _log_event(
                        self._logger,
                        logging.INFO,
                        "rover_gsa",
                        conn_id=session.id,
                        client_ip=session.addr.split(":", 1)[0],
                        user=session.username,
                        mountpoint=session.mountpoint,
                        fix_type=gsa[1],
                        pdop=gsa[3],
                        hdop=gsa[4],
                        vdop=gsa[5],
                        used=len(gsa[2]),
                    )
                continue
            if "GSV" in line:
                gsv = parse_gsv(line)
                if gsv:
                    session._last_nmea_ts_mono = now_monotonic()
                    total_sent, sent_num, total_sv, sats = gsv
                    # multi-sentence birleştirme
                    if sent_num == 1 or session._gsv_pending_total != total_sent:
                        session._gsv_pending_total = total_sent
                        session._gsv_pending_last_num = 1
                        session._gsv_pending_sats = []
                    session._gsv_pending_last_num = sent_num
                    session._gsv_pending_sats.extend(sats)
                    if sent_num == total_sent:
                        snrs = [s[3] for s in session._gsv_pending_sats if s[3] > 0]
                        mean_snr = (sum(snrs) / len(snrs)) if snrs else 0.0
                        session.last_gsv_snr_mean = mean_snr
                        session.last_gsv_total_sv = total_sv
                        snr_low = float(self._diag_cfg.get("snr_low", 25.0) or 25.0)
                        if mean_snr < snr_low:
                            self._event_add("SNR_LOW", mountpoint=session.mountpoint, conn_id=session.id, user=session.username, severity="info", msg="Zayıf sinyal (SNR düşük)", ctx={"snr": round(mean_snr, 1), "total_sv": total_sv})
                        try:
                            gga2 = session.last_gga
                            if isinstance(gga2, (list, tuple)) and len(gga2) >= 5:
                                nsat = int(gga2[3])
                                hdop = float(gga2[4])
                                fixq = int(gga2[2])
                                jam_snr = float(self._diag_cfg.get("jamming_snr", 20.0) or 20.0)
                                jam_nsat = int(self._diag_cfg.get("jamming_nsat", 8) or 8)
                                jam_hdop = float(self._diag_cfg.get("jamming_hdop", 2.5) or 2.5)
                                if mean_snr < jam_snr and nsat < jam_nsat and hdop > jam_hdop:
                                    session._jamming_suspect_ts_mono = now_monotonic()
                                    self._event_add(
                                        "JAMMING_SUSPECT",
                                        mountpoint=session.mountpoint,
                                        conn_id=session.id,
                                        user=session.username,
                                        severity="warning" if fixq == 0 else "info",
                                        msg="Olası jamming (SNR düşük + uydu düşük + HDOP yüksek)",
                                        ctx={"snr": round(mean_snr, 1), "nsat": nsat, "hdop": hdop, "fixq": fixq, "thr": {"snr": jam_snr, "nsat": jam_nsat, "hdop": jam_hdop}},
                                        throttle_s=30.0,
                                    )
                        except Exception:
                            pass
                        try:
                            device_id = session.username or f"{session.mountpoint}:{session.id}"
                            await self._shadow.upsert(device_id, {
                                "mountpoint": session.mountpoint,
                                "username": session.username,
                                "total_sv": total_sv,
                                "snr_mean": round(mean_snr, 1),
                            })
                        except Exception:
                            pass
                        _log_event(
                            self._logger,
                            logging.INFO,
                            "rover_gsv_series",
                            conn_id=session.id,
                            client_ip=session.addr.split(":", 1)[0],
                            user=session.username,
                            mountpoint=session.mountpoint,
                            total_sv=total_sv,
                            snr_mean=round(mean_snr, 1),
                        )
                continue

    async def _rover_send_loop(self, hub: MountpointHub, session: RoverSession) -> None:
        while True:
            chunk = await session.queue.get()
            if chunk is None:
                return
            if session.bucket is not None:
                while not session.bucket.consume(len(chunk)):
                    wait_s = session.bucket.time_to_available(len(chunk))
                    await asyncio.sleep(max(wait_s, 0.001))
            try:
                session.writer.write(chunk)
                await session.writer.drain()
            except Exception:
                try:
                    if not session.writer.is_closing():
                        session.writer.close()
                except Exception:
                    pass
                return
            session.sent_bytes += len(chunk)
            hub.record_sent_bytes(len(chunk))

    async def _send_health(self, writer: asyncio.StreamWriter, trace_id: str) -> None:
        ts0 = now_monotonic(); span_id = gen_span_id(); tp = build_traceparent(trace_id, span_id)
        body = b"OK\n"
        hdr = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"traceparent: " + tp.encode("ascii") + b"\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        writer.write(hdr)
        writer.write(body)
        await writer.drain()
        lat = int((now_monotonic()-ts0)*1000)
        _log_event(self._logger, logging.INFO, "response", status=200, latency_ms=lat, route="/healthz", trace_id=trace_id)

    async def _send_icy_ok(self, writer: asyncio.StreamWriter, trace_id: str) -> None:
        ts0 = now_monotonic(); span_id = gen_span_id(); tp = build_traceparent(trace_id, span_id)
        hdr = (
            b"ICY 200 OK\r\n"
            + b"Server: Asyncio-NTRIP-Caster\r\n"
            + b"Ntrip-Version: Ntrip/2.0\r\n"
            + (b"traceparent: " + tp.encode("ascii") + b"\r\n")
            + b"Content-Type: gnss/data\r\n"
            + b"Connection: close\r\n"
            + b"\r\n"
        )
        writer.write(hdr)
        await writer.drain()
        lat = int((now_monotonic()-ts0)*1000)
        _log_event(self._logger, logging.INFO, "response", status=200, latency_ms=lat, route="/ICY", trace_id=trace_id)

    async def _send_unauthorized(self, writer: asyncio.StreamWriter, trace_id: Optional[str] = None) -> None:
        tp = None
        if trace_id:
            tp = build_traceparent(trace_id, gen_span_id()).encode("ascii")
        hdr = (
            b"HTTP/1.1 401 Unauthorized\r\n"
            b"WWW-Authenticate: Basic realm=\"NTRIP\"\r\n"
            + ((b"traceparent: " + tp + b"\r\n") if tp else b"")
            + b"Connection: close\r\n"
            b"\r\n"
        )
        writer.write(hdr)
        await writer.drain()

    async def _send_forbidden(self, writer: asyncio.StreamWriter, trace_id: Optional[str] = None) -> None:
        tp = None
        if trace_id:
            tp = build_traceparent(trace_id, gen_span_id()).encode("ascii")
        hdr = b"HTTP/1.1 403 Forbidden\r\n" + ((b"traceparent: " + tp + b"\r\n") if tp else b"") + b"Connection: close\r\n\r\n"
        writer.write(hdr)
        await writer.drain()

    async def _send_too_many_requests(self, writer: asyncio.StreamWriter, trace_id: Optional[str] = None) -> None:
        tp = None
        if trace_id:
            tp = build_traceparent(trace_id, gen_span_id()).encode("ascii")
        hdr = b"HTTP/1.1 429 Too Many Requests\r\n" + ((b"traceparent: " + tp + b"\r\n") if tp else b"") + b"Connection: close\r\n\r\n"
        writer.write(hdr)
        await writer.drain()

    async def _send_source_forbidden(self, writer: asyncio.StreamWriter) -> None:
        writer.write(b"ERROR - Bad Password\r\n")
        await writer.drain()

    async def _send_sourcetable(self, writer: asyncio.StreamWriter, trace_id: str) -> None:
        ts0 = now_monotonic(); span_id = gen_span_id(); tp = build_traceparent(trace_id, span_id)
        body = build_sourcetable(
            mountpoints=self.list_mountpoints(),
            info=self.cfg.sourcetable,
            meta=(self.cfg.sourcetable.mountpoints_meta or None),
        )
        hdr = (
            b"SOURCETABLE 200 OK\r\n"
            b"Server: Asyncio-NTRIP-Caster\r\n"
            b"Content-Type: text/plain\r\n"
            b"traceparent: " + tp.encode("ascii") + b"\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        writer.write(hdr)
        writer.write(body)
        await writer.drain()
        lat = int((now_monotonic()-ts0)*1000)
        _log_event(self._logger, logging.INFO, "response", status=200, latency_ms=lat, route="/", trace_id=trace_id)

    async def _send_metrics(self, writer: asyncio.StreamWriter, trace_id: str) -> None:
        ts0 = now_monotonic(); span_id = gen_span_id(); tp = build_traceparent(trace_id, span_id)
        snaps = []
        for mp, hub in list(self._mountpoints.items()):
            snap = await hub.snapshot()
            snaps.append(snap)
        data = {
            "listen_port": self.bound_port(),
            "mountpoints": snaps,
            "rovers_total": self._rover_total,
        }
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        hdr = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"traceparent: " + tp.encode("ascii") + b"\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        writer.write(hdr)
        writer.write(body)
        await writer.drain()
        lat = int((now_monotonic()-ts0)*1000)
        _log_event(self._logger, logging.INFO, "response", status=200, latency_ms=lat, route="/metrics.json", trace_id=trace_id)

    async def _send_metrics_prom(self, writer: asyncio.StreamWriter, trace_id: str) -> None:
        ts0 = now_monotonic(); span_id = gen_span_id(); tp = build_traceparent(trace_id, span_id)
        lines = []
        lines.append("# HELP caster_rovers_total Current total rover sessions")
        lines.append("# TYPE caster_rovers_total gauge")
        lines.append(f"caster_rovers_total {self._rover_total}")
        lines.append("# TYPE caster_mountpoint_source_up gauge")
        lines.append("# TYPE caster_mountpoint_rovers gauge")
        lines.append("# TYPE caster_mountpoint_rx_bytes_total counter")
        lines.append("# TYPE caster_mountpoint_tx_bytes_total counter")
        lines.append("# TYPE caster_mountpoint_dropped_bytes_total counter")
        lines.append("# TYPE caster_mountpoint_rtcm_frames_total counter")
        lines.append("# TYPE caster_mountpoint_rtcm_crc_errors_total counter")
        lines.append("# TYPE caster_mountpoint_rtcm_messages_total counter")
        lines.append("# TYPE caster_mountpoint_station_id gauge")
        lines.append("# TYPE caster_mountpoint_antenna_height_m gauge")
        lines.append("# TYPE caster_mountpoint_last_rtcmtime_age_seconds gauge")
        lines.append("# TYPE caster_rover_last_nmea_age_seconds gauge")
        lines.append("# TYPE caster_rover_nmea_to_rtcmtime_delta_seconds gauge")
        for mp, hub in list(self._mountpoints.items()):
            snap = await hub.snapshot()
            mp_l = snap.get("mountpoint")
            src_up = 1 if snap.get("source_attached") else 0
            rv = int(snap.get("rover_count") or 0)
            rx = int(snap.get("rx_bytes_total") or 0)
            tx = int(snap.get("tx_bytes_total") or 0)
            dr = int(snap.get("dropped_bytes_total") or 0)
            lines.append(f"caster_mountpoint_source_up{{mountpoint=\"{mp_l}\"}} {src_up}")
            lines.append(f"caster_mountpoint_rovers{{mountpoint=\"{mp_l}\"}} {rv}")
            lines.append(f"caster_mountpoint_rx_bytes_total{{mountpoint=\"{mp_l}\"}} {rx}")
            lines.append(f"caster_mountpoint_tx_bytes_total{{mountpoint=\"{mp_l}\"}} {tx}")
            lines.append(f"caster_mountpoint_dropped_bytes_total{{mountpoint=\"{mp_l}\"}} {dr}")
            rf = int(snap.get("rtcm_frames_total") or 0)
            ce = int(snap.get("rtcm_crc_errors_total") or 0)
            lines.append(f"caster_mountpoint_rtcm_frames_total{{mountpoint=\"{mp_l}\"}} {rf}")
            lines.append(f"caster_mountpoint_rtcm_crc_errors_total{{mountpoint=\"{mp_l}\"}} {ce}")
            mc = snap.get("rtcm_msg_counts") or {}
            if isinstance(mc, dict):
                for k, v in mc.items():
                    try:
                        t = int(k)
                        c = int(v)
                    except Exception:
                        continue
                    lines.append(f"caster_mountpoint_rtcm_messages_total{{mountpoint=\"{mp_l}\",type=\"{t}\"}} {c}")

            si = snap.get("station_info") or {}
            if isinstance(si, dict):
                try:
                    stid = int(si.get("station_id") or 0)
                except Exception:
                    stid = 0
                if stid:
                    lines.append(f"caster_mountpoint_station_id{{mountpoint=\"{mp_l}\"}} {stid}")
                try:
                    ah = si.get("antenna_height_m")
                    if ah is not None:
                        lines.append(f"caster_mountpoint_antenna_height_m{{mountpoint=\"{mp_l}\"}} {float(ah)}")
                except Exception:
                    pass
            lra = snap.get("last_rtcmtime_age_s")
            if lra is not None:
                lines.append(f"caster_mountpoint_last_rtcmtime_age_seconds{{mountpoint=\"{mp_l}\"}} {float(lra)}")

            # rover freshness (sampled)
            for rv in (snap.get("rover_samples") or [])[:10]:
                try:
                    cid = int(rv.get("conn_id") or 0)
                except Exception:
                    cid = 0
                a1 = rv.get("last_nmea_age_s")
                a2 = rv.get("nmea_to_rtcmtime_delta_s")
                if a1 is not None:
                    lines.append(f"caster_rover_last_nmea_age_seconds{{mountpoint=\"{mp_l}\",conn_id=\"{cid}\"}} {float(a1)}")
                if a2 is not None:
                    lines.append(f"caster_rover_nmea_to_rtcmtime_delta_seconds{{mountpoint=\"{mp_l}\",conn_id=\"{cid}\"}} {float(a2)}")
        lines.append("# TYPE caster_auth_unauthorized_total counter")
        lines.append(f"caster_auth_unauthorized_total {self._auth_unauth_total}")
        for mp, c in self._auth_unauth_per_mp.items():
            lines.append(f"caster_auth_unauthorized_total{{mountpoint=\"{mp}\"}} {int(c)}")
        lines.append("# TYPE caster_source_unauthorized_total counter")
        lines.append(f"caster_source_unauthorized_total {self._source_unauth_total}")
        body_s = "\n".join(lines) + "\n"
        body = body_s.encode("utf-8")
        hdr = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; version=0.0.4\r\n"
            b"traceparent: " + tp.encode("ascii") + b"\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        writer.write(hdr)
        writer.write(body)
        await writer.drain()
        lat = int((now_monotonic()-ts0)*1000)
        _log_event(self._logger, logging.INFO, "response", status=200, latency_ms=lat, route="/metrics", trace_id=trace_id)


def configure_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        keys = {
            "event",
            "mountpoint",
            "user",
            "client_ip",
            "conn_id",
            "addr",
            "source_addr",
            "lat",
            "lon",
            "fixq",
            "nsat",
            "hdop",
            "vdop",
            "pdop",
            "fix_type",
            "used",
            "total_sv",
            "snr_mean",
            "speed_knots",
            "speed_kmh",
            "course",
            "sent_bytes",
            "dropped_bytes",
            "rx_bytes_total",
            "tx_bytes_total",
            "dropped_bytes_total",
            "rovers",
            "source",
            "crc_errors",
            "trace_id",
            "status",
            "latency_ms",
            "route",
            "err",
        }
        for k in keys:
            if hasattr(record, k):
                try:
                    payload[k] = getattr(record, k)
                except Exception:
                    payload[k] = str(getattr(record, k, None))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging_ex(cfg: LoggingCfg) -> None:
    lvl = getattr(logging, cfg.level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(lvl)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    if (cfg.fmt or "plain").lower() == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


def _log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    msg = event
    if (getattr(logger, "handlers", None) is None) or (not logger.handlers):
        if fields:
            msg = event + " " + " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    extra = {"event": event}
    for k, v in fields.items():
        if v is not None:
            extra[k] = v
    logger.log(level, msg, extra=extra)
