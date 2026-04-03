import base64
import unittest


from src.auth import UserRecord, parse_basic_auth, user_can_access, make_password_hash, verify_password
from src.utils import compile_ip_nets, ip_allowed



class TestAuth(unittest.TestCase):
    def test_parse_basic_auth_ok(self):
        token = base64.b64encode(b"user:pass").decode("ascii")
        v = parse_basic_auth(f"Basic {token}")
        self.assertEqual(v, ("user", "pass"))

    def test_parse_basic_auth_rejects_missing(self):
        self.assertIsNone(parse_basic_auth(""))
        self.assertIsNone(parse_basic_auth("Bearer abc"))
        self.assertIsNone(parse_basic_auth("Basic"))

    def test_user_can_access(self):
        u = UserRecord(username="u", password="p", password_sha256=None, password_hash=None, tier="t", mountpoints=("A", "B"))
        self.assertTrue(user_can_access(u, "A"))
        self.assertFalse(user_can_access(u, "C"))

        u2 = UserRecord(username="u", password="p", password_sha256=None, password_hash=None, tier="t", mountpoints=("*",))
        self.assertTrue(user_can_access(u2, "ANY"))

    def test_verify_password_pbkdf2(self):
        ph = make_password_hash("secret")
        u = UserRecord(username="u", password="", password_sha256=None, password_hash=ph, tier="t", mountpoints=("*",))
        self.assertTrue(verify_password(u, "secret"))
        self.assertFalse(verify_password(u, "wrong"))

    def test_ip_allow_deny(self):
        allow = compile_ip_nets(["10.0.0.0/24", "203.0.113.5"])
        deny = compile_ip_nets(["10.0.0.128/25"])  # deny upper half of 10.0.0.0/24
        self.assertTrue(ip_allowed("10.0.0.10", allow, deny))
        self.assertFalse(ip_allowed("10.0.0.200", allow, deny))
        self.assertTrue(ip_allowed("203.0.113.5", allow, deny))
