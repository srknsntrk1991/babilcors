import time
import unittest


from src.tiers import TokenBucket, epoch_gate_ok


class TestTiers(unittest.TestCase):
    def test_epoch_gate_ok(self):
        now = 1000.0
        self.assertTrue(epoch_gate_ok(None, 60, now))
        self.assertFalse(epoch_gate_ok(now, 60, now + 0.1))
        self.assertTrue(epoch_gate_ok(now, 60, now + 1.0))

    def test_token_bucket_wait_time(self):
        b = TokenBucket(rate_bps=10, capacity=10)
        self.assertTrue(b.consume(10))
        self.assertFalse(b.consume(1))
        wait = b.time_to_available(1)
        self.assertGreater(wait, 0.0)
        time.sleep(wait + 0.05)
        ok = b.consume(1)
        if not ok:
            time.sleep(0.05)
            ok = b.consume(1)
        self.assertTrue(ok)
