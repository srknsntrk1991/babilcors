import unittest

from src.rtcm import RtcmStreamParser, crc24q, parse_rtcm_1033


def build_rtcm_frame(msg_type: int, payload_bytes: int = 3) -> bytes:
    total_payload = bytearray(payload_bytes)
    total_payload[0] = (msg_type >> 4) & 0xFF
    total_payload[1] = ((msg_type & 0x0F) << 4) & 0xF0
    header = bytearray([0xD3, 0x00, len(total_payload) & 0xFF])
    frame_wo_crc = bytes(header + total_payload)
    crc = crc24q(frame_wo_crc)
    return frame_wo_crc + bytes([(crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF])


class TestRtcm(unittest.TestCase):
    def test_parser_valid_and_crc_error(self):
        p = RtcmStreamParser()
        f1 = build_rtcm_frame(1077, 5)
        f2 = build_rtcm_frame(1087, 6)
        frames, errs = p.feed(f1 + f2)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].msg_type, 1077)
        self.assertEqual(frames[1].msg_type, 1087)
        self.assertEqual(errs, 0)

        bad = bytearray(f1)
        bad[-1] ^= 0xFF
        frames2, errs2 = p.feed(bytes(bad))
        self.assertEqual(len(frames2), 0)
        self.assertGreaterEqual(errs2, 1)

    def test_parse_1033_minimal(self):
        stid = 1
        ant = b"ANTTEST"
        ant_serial = b"ASER"
        rx = b"RXTEST"
        ver = b"1.0"
        ser = b"RSER"
        mt = 1033
        p = bytearray()
        p.append((mt >> 4) & 0xFF)
        p.append(((mt & 0x0F) << 4) | ((stid >> 8) & 0x0F))
        p.append(stid & 0xFF)
        p.append(len(ant) & 0xFF)
        p.extend(ant)
        p.append(0)  # antenna setup id
        p.append(len(ant_serial) & 0xFF)
        p.extend(ant_serial)
        p.append(len(rx) & 0xFF)
        p.extend(rx)
        p.append(len(ver) & 0xFF)
        p.extend(ver)
        p.append(len(ser) & 0xFF)
        p.extend(ser)
        info = parse_rtcm_1033(bytes(p))
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.get("antenna_descriptor"), "ANTTEST")
        self.assertEqual(info.get("receiver_descriptor"), "RXTEST")
