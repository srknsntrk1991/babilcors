import unittest

from src.utils import split_path_query


class TestUtils(unittest.TestCase):
    def test_split_path_query(self):
        p, q = split_path_query("/admin/kick?mountpoint=KNY1&conn_id=42")
        self.assertEqual(p, "/admin/kick")
        self.assertEqual(q["mountpoint"], "KNY1")
        self.assertEqual(q["conn_id"], "42")

