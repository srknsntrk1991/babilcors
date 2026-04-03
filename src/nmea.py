from __future__ import annotations

from typing import List, Optional, Tuple


def _checksum_ok(sentence: str) -> bool:
    if "*" not in sentence:
        return False
    body, _, cs = sentence.rpartition("*")
    try:
        val = int(cs.strip(), 16)
    except ValueError:
        return False
    x = 0
    for ch in body[1:]:
        x ^= ord(ch)
    return x == val


def parse_gga(line: str) -> Optional[Tuple[float, float, int, int, float]]:
    if not line.startswith("$") or "*" not in line:
        return None
    if not _checksum_ok(line):
        return None
    parts = line.split(",")
    if len(parts) < 15:
        return None
    lat = _parse_lat(parts[2], parts[3])
    lon = _parse_lon(parts[4], parts[5])
    try:
        fix_q = int(parts[6])
    except ValueError:
        return None
    try:
        nsat = int(parts[7])
    except ValueError:
        nsat = 0
    try:
        hdop = float(parts[8])
    except ValueError:
        hdop = 0.0
    if lat is None or lon is None:
        return None
    return (lat, lon, fix_q, nsat, hdop)


def parse_gsa(line: str) -> Optional[Tuple[str, int, List[int], float, float, float]]:
    if not line.startswith("$") or "*" not in line:
        return None
    if not _checksum_ok(line):
        return None
    parts = line.split(",")
    if len(parts) < 18:
        return None
    mode = parts[1] or ""
    try:
        fix_type = int(parts[2] or "0")  # 1=no fix,2=2D,3=3D
    except ValueError:
        fix_type = 0
    sats: List[int] = []
    for i in range(3, 15):
        p = parts[i].strip()
        if not p:
            continue
        try:
            sats.append(int(p))
        except ValueError:
            continue
    def f(x: str) -> float:
        try:
            return float(x)
        except Exception:
            return 0.0
    pdop = f(parts[15])
    hdop = f(parts[16])
    vdop = f(parts[17].split("*")[0])
    return (mode, fix_type, sats, pdop, hdop, vdop)


def parse_gsv(line: str) -> Optional[Tuple[int, int, int, List[Tuple[int, int, int, int]]]]:
    if not line.startswith("$") or "*" not in line:
        return None
    if not _checksum_ok(line):
        return None
    parts = line.split(",")
    if len(parts) < 4:
        return None
    try:
        total_sent = int(parts[1])
        sent_num = int(parts[2])
        total_sv = int(parts[3])
    except ValueError:
        return None
    sats: List[Tuple[int, int, int, int]] = []
    # groups of 4 fields: PRN, elev, azimuth, SNR
    i = 4
    while i + 3 < len(parts):
        try:
            prn = int(parts[i]) if parts[i] else 0
            elev = int(parts[i+1]) if parts[i+1] else 0
            az = int(parts[i+2]) if parts[i+2] else 0
            snr_str = parts[i+3].split("*")[0]
            snr = int(snr_str) if snr_str else 0
            if prn:
                sats.append((prn, elev, az, snr))
        except Exception:
            pass
        i += 4
    return (total_sent, sent_num, total_sv, sats)


def _parse_lat_lon(lat_str: str, ns: str, lon_str: str, ew: str) -> Tuple[float, float]:
    def conv(v: str) -> float:
        if not v:
            return 0.0
        try:
            x = float(v)
        except Exception:
            return 0.0
        deg = int(x // 100)
        mins = x - (deg * 100)
        return float(deg) + (mins / 60.0)

    lat = conv(lat_str)
    lon = conv(lon_str)
    if ns.upper() == 'S':
        lat = -abs(lat)
    if ew.upper() == 'W':
        lon = -abs(lon)
    return lat, lon


def parse_rmc(line: str) -> Optional[Tuple[str, float, float, float, float, str]]:
    if not line.startswith("$") or "*" not in line:
        return None
    if not _checksum_ok(line):
        return None
    parts = line.split(',')
    if len(parts) < 12:
        return None
    status = parts[2]
    try:
        lat, lon = _parse_lat_lon(parts[3], parts[4], parts[5], parts[6])
    except Exception:
        lat, lon = 0.0, 0.0
    try:
        spd = float(parts[7] or 0.0)  # knots
    except Exception:
        spd = 0.0
    try:
        crs = float(parts[8] or 0.0)
    except Exception:
        crs = 0.0
    date = parts[9] if len(parts) > 9 else ""
    return (status, lat, lon, spd, crs, date)


def parse_vtg(line: str) -> Optional[Tuple[float, float]]:
    if not line.startswith("$") or "*" not in line:
        return None
    if not _checksum_ok(line):
        return None
    parts = line.split(',')
    if len(parts) < 9:
        return None
    try:
        crs = float(parts[1] or 0.0)
    except Exception:
        crs = 0.0
    try:
        spd_kmh = float(parts[7] or 0.0)
    except Exception:
        spd_kmh = 0.0
    return (crs, spd_kmh)


def parse_zda(line: str) -> Optional[Tuple[str, int, int, int]]:
    if not line.startswith("$") or "*" not in line:
        return None
    if not _checksum_ok(line):
        return None
    parts = line.split(',')
    if len(parts) < 5:
        return None
    time_str = parts[1]
    try:
        day = int(parts[2] or 0)
        month = int(parts[3] or 0)
        year = int(parts[4] or 0)
    except Exception:
        return None
    return (time_str, day, month, year)


def _parse_lat(val: str, hemi: str) -> Optional[float]:
    if not val:
        return None
    try:
        deg = int(val[:2])
        minutes = float(val[2:])
    except ValueError:
        return None
    sign = -1 if hemi == "S" else 1
    return sign * (deg + minutes / 60.0)


def _parse_lon(val: str, hemi: str) -> Optional[float]:
    if not val:
        return None
    try:
        deg = int(val[:3])
        minutes = float(val[3:])
    except ValueError:
        return None
    sign = -1 if hemi == "W" else 1
    return sign * (deg + minutes / 60.0)
