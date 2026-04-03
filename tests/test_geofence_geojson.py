import unittest

from src.geofence import geojson_to_rings


class TestGeojson(unittest.TestCase):
    def test_polygon(self):
        gj = {"type": "Polygon", "coordinates": [[[32.0, 37.0], [33.0, 37.0], [33.0, 38.0], [32.0, 37.0]]]} 
        rings = geojson_to_rings(gj)
        self.assertEqual(len(rings), 1)
        self.assertAlmostEqual(rings[0][0][0], 37.0, places=6)

    def test_multipolygon(self):
        gj = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[32.0, 37.0], [33.0, 37.0], [33.0, 38.0], [32.0, 37.0]]],
                [[[30.0, 35.0], [31.0, 35.0], [31.0, 36.0], [30.0, 35.0]]],
            ],
        }
        rings = geojson_to_rings(gj)
        self.assertEqual(len(rings), 2)

