import unittest

from src.nmea import parse_gsa, parse_gsv, parse_rmc, parse_vtg, parse_zda


def add_checksum(sentence: str) -> str:
    x = 0
    for ch in sentence[1:]:
        x ^= ord(ch)
    return sentence + "*" + format(x, "02X")


class TestNmea(unittest.TestCase):
    def test_parse_gsa(self):
        base = "$GPGSA,A,3,04,05,09,12,24,25,29,31,,,,,1.8,1.0,1.5"
        s = add_checksum(base)
        gsa = parse_gsa(s)
        self.assertIsNotNone(gsa)
        assert gsa is not None
        self.assertEqual(gsa[1], 3)
        self.assertGreaterEqual(len(gsa[2]), 1)

    def test_parse_gsv(self):
        base = "$GPGSV,2,1,08,01,40,083,41,02,17,308,00,03,13,172,43,04,09,304,45"
        s = add_checksum(base)
        gsv = parse_gsv(s)
        self.assertIsNotNone(gsv)
        assert gsv is not None
        self.assertEqual(gsv[0], 2)
        self.assertEqual(gsv[1], 1)
        self.assertEqual(gsv[2], 8)
        self.assertGreaterEqual(len(gsv[3]), 1)

    def test_parse_rmc(self):
        base = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W"
        s = add_checksum(base)
        rmc = parse_rmc(s)
        self.assertIsNotNone(rmc)
        assert rmc is not None
        self.assertEqual(rmc[0], "A")

    def test_parse_vtg(self):
        base = "$GPVTG,054.7,T,034.4,M,005.5,N,010.2,K"
        s = add_checksum(base)
        vtg = parse_vtg(s)
        self.assertIsNotNone(vtg)

    def test_parse_zda(self):
        base = "$GPZDA,201530.00,04,07,2002,00,00"
        s = add_checksum(base)
        zda = parse_zda(s)
        self.assertIsNotNone(zda)
