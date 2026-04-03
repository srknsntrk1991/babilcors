import time
import uuid
from typing import Any, Dict, Optional, Tuple

try:
    import jwt
except Exception:
    jwt = None

try:
    import redis.asyncio as redis  # type: ignore
except Exception:
    redis = None


class JwtManager:
    def __init__(
        self,
        *,
        secret: str,
        access_exp_s: int = 900,
        refresh_exp_s: int = 7 * 24 * 3600,
        redis_url: str = "",
    ) -> None:
        self.secret = secret
        self.access_exp_s = int(access_exp_s)
        self.refresh_exp_s = int(refresh_exp_s)
        self.redis_url = redis_url
        self._r = None
        self._revoked: Dict[str, int] = {}

    async def start(self) -> None:
        if not self.redis_url or redis is None:
            return
        if self._r is None:
            self._r = redis.from_url(self.redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._r is not None:
            try:
                await self._r.close()
            except Exception:
                pass
            self._r = None

    def _make(self, *, sub: str, role: str, typ: str, exp_s: int) -> Tuple[str, Dict[str, Any]]:
        if jwt is None:
            raise RuntimeError("pyjwt not installed")
        now = int(time.time())
        exp = now + int(exp_s)
        jti = uuid.uuid4().hex
        claims = {"sub": sub, "role": role, "type": typ, "iat": now, "exp": exp, "jti": jti}
        token = jwt.encode(claims, self.secret, algorithm="HS256")
        return token, claims

    def mint_access(self, *, sub: str, role: str) -> Tuple[str, Dict[str, Any]]:
        return self._make(sub=sub, role=role, typ="access", exp_s=self.access_exp_s)

    def mint_refresh(self, *, sub: str, role: str) -> Tuple[str, Dict[str, Any]]:
        return self._make(sub=sub, role=role, typ="refresh", exp_s=self.refresh_exp_s)

    async def revoke_jti(self, jti: str, exp: int) -> None:
        jti = str(jti)
        exp = int(exp)
        if exp <= int(time.time()):
            return
        if self._r is not None:
            ttl = max(exp - int(time.time()), 1)
            await self._r.setex(f"jwtrev:{jti}", ttl, "1")
            return
        self._revoked[jti] = exp

    async def is_revoked(self, jti: str) -> bool:
        jti = str(jti)
        if self._r is not None:
            v = await self._r.get(f"jwtrev:{jti}")
            return bool(v)
        exp = self._revoked.get(jti)
        if exp is None:
            return False
        if exp <= int(time.time()):
            try:
                del self._revoked[jti]
            except Exception:
                pass
            return False
        return True

    async def decode(self, token: str, expected_type: str) -> Optional[Dict[str, Any]]:
        if jwt is None:
            return None
        try:
            claims = jwt.decode(token, self.secret, algorithms=["HS256"])
        except Exception:
            return None
        if str(claims.get("type")) != expected_type:
            return None
        jti = str(claims.get("jti") or "")
        if jti and await self.is_revoked(jti):
            return None
        return claims

    @staticmethod
    def role_allows(role: str, *, write: bool) -> bool:
        r = (role or "").lower()
        if r in ("admin", "superadmin"):
            return True
        if r in ("admin_ro", "admin_readonly", "readonly_admin"):
            return not write
        return False
