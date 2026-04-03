import asyncio
import json
import tempfile
import unittest

from src.caster import NtripCaster, load_config, validate_config


class TestUserRoleUpdate(unittest.IsolatedAsyncioTestCase):
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
                    "users": {"demo": {"password": "demo", "tier": "free", "mountpoints": ["*"]}},
                    "security": {"admin_token": "admintoken", "admin_rate_limit_per_min": 1000},
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
        data = await r.read(4096)
        w.close()
        await w.wait_closed()
        return data

    async def test_patch_role(self):
        body = b"{\"role\":\"admin_ro\"}"
        raw = (
            b"PATCH /admin/users/demo HTTP/1.0\r\n"
            b"Authorization: Bearer admintoken\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
        )
        resp = await self._req(raw)
        self.assertIn(b"200", resp)

