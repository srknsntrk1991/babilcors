import unittest

from src.diagnostics import compute_alerts


class TestDiagnostics(unittest.TestCase):
    def test_no_source_alert(self):
        snaps = [{"mountpoint": "KNY1", "source_attached": False, "rover_samples": []}]
        alerts = compute_alerts(snaps)
        self.assertTrue(any(a.code == "NO_SOURCE" for a in alerts))
        a = [x for x in alerts if x.code == "NO_SOURCE"][0]
        self.assertTrue("recommended_actions" in (a.ctx or {}))

    def test_threshold_override(self):
        snaps = [{"mountpoint": "KNY1", "source_attached": True, "last_rtcmtime_age_s": 2.0, "diagnostics_cfg": {"rtcm_stale_s": 1.0}, "rover_samples": []}]
        alerts = compute_alerts(snaps)
        self.assertTrue(any(a.code == "RTCM_STALE" for a in alerts))

    def test_jam_spoof_geofence_flags(self):
        snaps = [
            {
                "mountpoint": "KNY1",
                "source_attached": True,
                "rover_samples": [
                    {
                        "conn_id": 1,
                        "user": "u",
                        "geofence_violation_recent": True,
                        "jamming_suspect_recent": True,
                        "spoofing_suspect_recent": True,
                    }
                ],
            }
        ]
        alerts = compute_alerts(snaps)
        codes = {a.code for a in alerts}
        self.assertIn("GEOFENCE_VIOLATION", codes)
        self.assertIn("JAMMING_SUSPECT", codes)
        self.assertIn("SPOOFING_SUSPECT", codes)
