from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


CRC24Q_POLY = 0x1864CFB


def crc24q(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= (b & 0xFF) << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= CRC24Q_POLY
    return crc & 0xFFFFFF


def rtcm_message_type(payload: bytes) -> Optional[int]:
    if len(payload) < 2:
        return None
    return ((payload[0] << 4) | (payload[1] >> 4)) & 0xFFF


@dataclass
class RtcmFrame:
    msg_type: int
    raw: bytes


class RtcmStreamParser:
    def __init__(self):
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> Tuple[List[RtcmFrame], int]:
        if chunk:
            self._buf.extend(chunk)
        frames: List[RtcmFrame] = []
        crc_errors = 0

        i = 0
        while True:
            n = len(self._buf)
            while i < n and self._buf[i] != 0xD3:
                i += 1
            if i > 0:
                del self._buf[:i]
                i = 0
                n = len(self._buf)
            if n < 3:
                break

            length = ((self._buf[1] & 0x03) << 8) | self._buf[2]
            total = 3 + length + 3
            if n < total:
                break

            frame = bytes(self._buf[:total])
            payload = frame[3 : 3 + length]
            crc_expected = (frame[-3] << 16) | (frame[-2] << 8) | frame[-1]
            crc_calc = crc24q(frame[:-3])
            if crc_calc != crc_expected:
                crc_errors += 1
                del self._buf[0:1]
                continue

            mt = rtcm_message_type(payload)
            if mt is None:
                crc_errors += 1
                del self._buf[0:1]
                continue
            frames.append(RtcmFrame(msg_type=mt, raw=frame))
            del self._buf[:total]

        return frames, crc_errors


class _BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.bitpos = 0

    def skip(self, nbits: int) -> None:
        self.bitpos += nbits

    def read_bits(self, nbits: int) -> int:
        val = 0
        for _ in range(nbits):
            byte_idx = self.bitpos // 8
            off = 7 - (self.bitpos % 8)
            if byte_idx >= len(self.data):
                raise IndexError("bit read overflow")
            bit = (self.data[byte_idx] >> off) & 1
            val = (val << 1) | bit
            self.bitpos += 1
        return val

    def read_bytes(self, n: int) -> bytes:
        # align to next byte
        if self.bitpos % 8 != 0:
            self.bitpos += (8 - (self.bitpos % 8))
        byte_idx = self.bitpos // 8
        end = byte_idx + n
        if end > len(self.data):
            end = len(self.data)
        self.bitpos += n * 8
        return bytes(self.data[byte_idx:end])


def parse_rtcm_1033(payload: bytes) -> Optional[dict]:
    try:
        br = _BitReader(payload)
        br.skip(12)  # message type (already known)
        _station_id = br.read_bits(12)
        ant_desc_len = br.read_bits(8)
        ant_desc = br.read_bytes(ant_desc_len).decode("ascii", errors="ignore").strip()
        _ant_setup_id = br.read_bits(8)
        ant_serial_len = br.read_bits(8)
        ant_serial = br.read_bytes(ant_serial_len).decode("ascii", errors="ignore").strip()
        rx_desc_len = br.read_bits(8)
        rx_desc = br.read_bytes(rx_desc_len).decode("ascii", errors="ignore").strip()
        rx_ver_len = br.read_bits(8)
        rx_ver = br.read_bytes(rx_ver_len).decode("ascii", errors="ignore").strip()
        rx_serial_len = br.read_bits(8)
        rx_serial = br.read_bytes(rx_serial_len).decode("ascii", errors="ignore").strip()
        return {
            "antenna_descriptor": ant_desc,
            "antenna_serial": ant_serial,
            "receiver_descriptor": rx_desc,
            "receiver_version": rx_ver,
            "receiver_serial": rx_serial,
        }
    except Exception:
        return None


def parse_rtcm_1005_1006(payload: bytes) -> Optional[dict]:
    try:
        br = _BitReader(payload)
        mt = br.read_bits(12)
        if mt not in (1005, 1006):
            return None
        stid = br.read_bits(12)
        br.read_bits(6)  # itrf
        br.read_bits(1)  # gps
        br.read_bits(1)  # glonass
        br.read_bits(1)  # galileo
        br.read_bits(1)  # refstation

        def s38() -> float:
            v = br.read_bits(38)
            if v & (1 << 37):
                v = v - (1 << 38)
            return float(v) * 0.0001

        x = s38()
        br.read_bits(2)
        y = s38()
        br.read_bits(2)
        z = s38()
        h = None
        if mt == 1006:
            hv = br.read_bits(16)
            h = float(hv) * 0.0001
        return {"station_id": stid, "ecef_x_m": x, "ecef_y_m": y, "ecef_z_m": z, "antenna_height_m": h}
    except Exception:
        return None


def parse_rtcm_1007_1008(payload: bytes) -> Optional[dict]:
    try:
        br = _BitReader(payload)
        mt = br.read_bits(12)
        if mt not in (1007, 1008):
            return None
        _station_id = br.read_bits(12)
        desc_len = br.read_bits(8)
        desc = br.read_bytes(desc_len).decode("ascii", errors="ignore").strip()
        _setup_id = br.read_bits(8)
        serial = ""
        if mt == 1008:
            s_len = br.read_bits(8)
            serial = br.read_bytes(s_len).decode("ascii", errors="ignore").strip()
        out = {"antenna_descriptor": desc}
        if serial:
            out["antenna_serial"] = serial
        return out
    except Exception:
        return None
