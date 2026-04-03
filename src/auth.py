from __future__ import annotations

import binascii
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple

from .utils import b64decode_str, safe_decode


@dataclass(frozen=True)
class UserRecord:
    username: str
    password: str
    password_sha256: Optional[str]
    password_hash: Optional[str]
    tier: str
    mountpoints: Tuple[str, ...]
    geofence_id: Optional[str] = None
    role: str = "user"


def parse_basic_auth(header_value: str) -> Optional[Tuple[str, str]]:
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2:
        return None
    scheme, b64 = parts
    if scheme.lower() != "basic":
        return None
    raw = b64decode_str(b64)
    if not raw:
        return None
    s = safe_decode(raw)
    if ":" not in s:
        return None
    user, pwd = s.split(":", 1)
    if not user:
        return None
    return (user, pwd)


def _resolve_secret(value: str) -> str:
    if value.startswith("env:"):
        k = value.split(":", 1)[1]
        return os.getenv(k, "")
    return value


def _get_str(d: Dict[str, Any], key: str, default: str = "") -> str:
    v = d.get(key, default)
    if isinstance(v, str):
        return v
    return str(v)


def verify_password(user: "UserRecord", provided: str) -> bool:
    if user.password_hash:
        parts = user.password_hash.split("$")
        if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
            try:
                iterations = int(parts[1])
                salt = binascii.unhexlify(parts[2].encode("ascii"))
                expected = binascii.unhexlify(parts[3].encode("ascii"))
            except Exception:
                return False
            derived = hashlib.pbkdf2_hmac("sha256", provided.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(derived, expected)
    if user.password_sha256:
        h = hashlib.sha256(provided.encode("utf-8")).hexdigest()
        return h.lower() == user.password_sha256.lower()
    return user.password == provided


def make_password_hash(password: str, *, iterations: int = 210_000) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        binascii.hexlify(salt).decode("ascii"),
        binascii.hexlify(derived).decode("ascii"),
    )


def load_users(users_obj: Dict[str, Any]) -> Dict[str, UserRecord]:
    out: Dict[str, UserRecord] = {}
    for username, ud in (users_obj or {}).items():
        if not isinstance(ud, dict):
            continue
        pwd_raw = _get_str(ud, "password", "")
        pwd = _resolve_secret(pwd_raw)
        ph_raw = _get_str(ud, "password_sha256", "")
        ph = _resolve_secret(ph_raw) if ph_raw else None
        p_hash_raw = _get_str(ud, "password_hash", "")
        p_hash = _resolve_secret(p_hash_raw) if p_hash_raw else None
        tier = str(ud.get("tier", "free"))
        mps = ud.get("mountpoints", ["*"])
        if isinstance(mps, list):
            mountpoints = tuple(str(x) for x in mps)
        else:
            mountpoints = ("*",)
        out[str(username)] = UserRecord(
            username=str(username),
            password=pwd,
            password_sha256=ph,
            password_hash=p_hash,
            tier=tier,
            mountpoints=mountpoints,
            geofence_id=str(ud.get("geofence_id")) if ud.get("geofence_id") else None,
            role=str(ud.get("role", "user")),
        )
    return out


def user_can_access(user: UserRecord, mountpoint: str) -> bool:
    if "*" in user.mountpoints:
        return True
    return mountpoint in user.mountpoints
