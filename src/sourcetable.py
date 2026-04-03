from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class SourcetableInfo:
    operator: str
    country: str
    network: str = ""
    mountpoints_meta: Optional[Dict[str, Dict[str, Any]]] = None


def build_sourcetable(
    *,
    mountpoints: Iterable[str],
    info: SourcetableInfo,
    meta: Optional[Dict[str, Dict[str, Any]]] = None,
) -> bytes:
    lines: List[str] = []
    meta_map = meta or info.mountpoints_meta or {}
    for mp in sorted(set(mountpoints)):
        m = meta_map.get(mp, {}) if isinstance(meta_map, dict) else {}
        ident = str(m.get("identifier") or "RTCM32")
        fmt = str(m.get("format") or "RTCM 3.2")
        fmt_details = str(m.get("format_details") or "")
        carrier = str(m.get("carrier") if m.get("carrier") is not None else 2)
        nav = str(m.get("nav_system") or "GPS+GLO+GAL+BDS+QZSS+SBAS")
        network = str(m.get("network") or info.network or info.operator)
        country = str(m.get("country") or info.country)
        lat = float(m.get("latitude") or 0.0)
        lon = float(m.get("longitude") or 0.0)
        nmea = str(m.get("nmea") if m.get("nmea") is not None else 0)
        sol = str(m.get("solution") if m.get("solution") is not None else 0)
        gen = str(m.get("generator") or info.operator)
        comp = str(m.get("compression") or "none")
        auth = str(m.get("authentication") or "B")
        fee = str(m.get("fee") or "N")
        bitrate = str(m.get("bitrate") if m.get("bitrate") is not None else 0)
        misc_items = []
        ant = m.get("antenna")
        rx = m.get("receiver")
        fw = m.get("firmware")
        datum = m.get("datum")
        if ant:
            misc_items.append(f"ANT={ant}")
        if rx:
            misc_items.append(f"RX={rx}")
        if fw:
            misc_items.append(f"FW={fw}")
        if datum:
            misc_items.append(f"DATUM={datum}")
        misc = " ".join(misc_items)
        lines.append(
            "STR;{mp};{ident};{fmt};{fmt_details};{carrier};{nav};{network};{country};{lat:.6f};{lon:.6f};{nmea};{sol};{gen};{comp};{auth};{fee};{bitrate};{misc}".format(
                mp=mp,
                ident=ident,
                fmt=fmt,
                fmt_details=fmt_details,
                carrier=carrier,
                nav=nav,
                network=network,
                country=country,
                lat=lat,
                lon=lon,
                nmea=nmea,
                sol=sol,
                gen=gen,
                comp=comp,
                auth=auth,
                fee=fee,
                bitrate=bitrate,
                misc=misc,
            )
        )
    lines.append("ENDSOURCETABLE")
    body = "\r\n".join(lines) + "\r\n"
    return body.encode("ascii", errors="ignore")
