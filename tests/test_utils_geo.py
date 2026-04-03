import unittest

from src.utils import ecef_to_geodetic


class TestGeo(unittest.TestCase):
    def test_ecef_to_geodetic_equator(self):
        lat, lon, h = ecef_to_geodetic(6378137.0, 0.0, 0.0)
        self.assertAlmostEqual(lat, 0.0, places=3)
        self.assertAlmostEqual(lon, 0.0, places=3)
        self.assertAlmostEqual(h, 0.0, places=1)

