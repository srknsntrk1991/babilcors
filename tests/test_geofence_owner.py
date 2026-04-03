import asyncio
import json
import tempfile
import unittest

from src.caster import NtripCaster, load_config, validate_config


class TestGeofenceOwner(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json")
        self.tmp.write(
            json.dumps(
                {
                    "listen": {"host": "127.0.0.1", "port": 0, "backlog": 10, "reuse_port": False},
                    "logging": {"level": "INFO", "format": "plain"},
                    "sourcetable": {"operator": "TEST", "country": "TR"},
                    "sources": {"password": "sourcepass", "mountpoints": ["KNY1"]},
                    "tiers": {"free": {"rate_limit_bps": 0, "max_epochs_per_minute": 0, "max_queue_bytes": 262144}},
                    "users": {
                        "alice": {"password": "a", "tier": "free", "mountpoints": ["*"], "role": "geofence_editor"},
                        "bob": {"password": "b", "tier": "free", "mountpoints": ["*"], "role": "geofence_editor"}
                    },
                    "security": {"admin_token": "admintoken", "admin_rate_limit_per_min": 1000, "admin_jwt_secret": "secret"},
                }
            )
        )
        self.tmp.close()
        cfg = load_config(self.tmp.name)
        validate_config(cfg)
        self.caster = NtripCaster(cfg, cfg_path=self.tmp.name)
        await self.caster.start()
        self.port = self.caster.bound_port()
        assert self.port is not None

    async def asyncTearDown(self):
        await self.caster.close()

    async def _req(self, raw: bytes) -> bytes:
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(raw)
        await w.drain()
        data = await r.read(65536)
        w.close()
        await w.wait_closed()
        return data

    async def _login(self, user: str, pwd: str) -> str:
        body = json.dumps({"username": user, "password": pwd}).encode("utf-8")
        raw = (
            b"POST /admin/login HTTP/1.0\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
        )
        resp = await self._req(raw)
        self.assertIn(b"200", resp)
        payload = resp.split(b"\r\n\r\n", 1)[1]
        obj = json.loads(payload.decode("utf-8"))
        return obj["access"]

    async def test_owner_enforced(self):
        a_tok = await self._login("alice", "a")
        b_tok = await self._login("bob", "b")

        gf_body = json.dumps({"id": "A1", "mode": "alert", "polygon": [[37.0, 32.0], [37.1, 32.0], [37.1, 32.1], [37.0, 32.0]]}).encode("utf-8")
        raw_create = (
            b"POST /admin/geofences HTTP/1.0\r\n"
            + b"Authorization: Bearer " + a_tok.encode("utf-8") + b"\r\n"
            + b"Content-Type: application/json\r\n"
            + b"Content-Length: " + str(len(gf_body)).encode("ascii") + b"\r\n\r\n" + gf_body
        )
        resp = await self._req(raw_create)
        self.assertIn(b"201", resp)

        raw_list_bob = (
            b"GET /admin/geofences HTTP/1.0\r\n"
            + b"Authorization: Bearer " + b_tok.encode("utf-8") + b"\r\n\r\n"
        )
        resp2 = await self._req(raw_list_bob)
        payload2 = resp2.split(b"\r\n\r\n", 1)[1]
        obj2 = json.loads(payload2.decode("utf-8"))
        self.assertEqual(obj2.get("geofences"), {})

        raw_del_bob = (
            b"DELETE /admin/geofences/A1 HTTP/1.0\r\n"
            + b"Authorization: Bearer " + b_tok.encode("utf-8") + b"\r\n\r\n"
        )
        resp3 = await self._req(raw_del_bob)
        self.assertIn(b"403", resp3)

