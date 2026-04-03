from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Alert:
    code: str
    severity: str
    message: str
    mountpoint: str
    conn_id: Optional[int] = None
    user: Optional[str] = None
    ctx: Optional[Dict[str, Any]] = None


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _thr(cfg: Dict[str, Any], key: str, default: float) -> float:
    try:
        v = cfg.get(key)
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _recommendations(code: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    if code == "NO_SOURCE":
        return {
            "probable_causes": ["Base (SOURCE) bağlantısı yok", "Yanlış kaynak şifresi", "Mountpoint disabled", "Ağ/firewall sorunu"],
            "recommended_actions": ["Base cihazının caster’a bağlı olduğunu doğrula", "SOURCE şifresini ve mountpoint adını kontrol et", "Admin’den mountpoint enable et", "Firewall/NAT ve port erişimini kontrol et"],
        }
    if code == "RTCM_STALE":
        return {
            "probable_causes": ["Base uplink kesintisi", "RTCM üretimi durdu", "Ağ gecikmesi/packet loss"],
            "recommended_actions": ["Base cihaz loglarını ve uplink’i kontrol et", "RTCM çıkış ayarlarını kontrol et", "Ağ gecikmesi için ping/trace yap"],
        }
    if code == "NMEA_STALE":
        return {
            "probable_causes": ["Rover NMEA uplink göndermiyor", "Cihaz NMEA output kapalı", "TCP bağlantısı tek yönlü"],
            "recommended_actions": ["Rover’da NMEA (GGA) output açık mı kontrol et", "Caster’a Ntrip-GGA/stream NMEA akışı var mı kontrol et", "Mobil ağ/NAT keepalive ayarlarını gözden geçir"],
        }
    if code in ("SNR_LOW", "NSAT_LOW"):
        return {
            "probable_causes": ["Kapalı alan/engel", "Anten konumu kötü", "RF girişimi/jamming"],
            "recommended_actions": ["Anten’i açık gökyüzüne taşı", "Kablo/konnektörleri kontrol et", "Yakında RF parazit kaynağı var mı incele"],
        }
    if code == "NO_FIX":
        return {
            "probable_causes": ["Düzeltme yok veya kalitesiz", "Anten/RF sorunu", "Uydu görünürlüğü düşük"],
            "recommended_actions": ["Doğru mountpoint’e bağlı olduğunu doğrula", "Anten ve kabloyu kontrol et", "SNR/NSAT/HDOP değerlerini takip et"],
        }
    if code == "ANTENNA_OR_RF_SUSPECT":
        return {
            "probable_causes": ["Anten kablosu/konnektör arızası", "LNA beslemesi yok", "Anten kısa devre/temassızlık"],
            "recommended_actions": ["Anten kablosunu ve konnektörleri yeniden tak", "Anten beslemesini/LNA’yı kontrol et", "Farklı anten/kablo ile dene"],
        }
    if code == "JAMMING_SUSPECT":
        return {
            "probable_causes": ["RF jamming", "Güçlü parazit kaynağı", "Anten yakınında elektronik gürültü"],
            "recommended_actions": ["Cihazı parazit kaynağından uzaklaştır", "Band-pass filtre/kaliteli anten kullan", "Saha haritasında jamming kümelenmesini kontrol et"],
        }
    if code == "SPOOFING_SUSPECT":
        return {
            "probable_causes": ["GNSS spoofing", "Cihaz konum filtrelemesi kapalı", "Yanlış datum/format"],
            "recommended_actions": ["Cihazda anti-spoof/RAIM özelliklerini aç", "IMU/odometre ile çapraz doğrula", "Zaman/konum sıçramalarını logla ve karşılaştır"],
        }
    if code == "GEOFENCE_VIOLATION":
        return {
            "probable_causes": ["Cihaz izinli bölge dışına çıktı", "Geofence polygon hatalı çizildi", "Yanlış geofence ataması"],
            "recommended_actions": ["Haritada konumu ve geofence’i karşılaştır", "Gerekirse polygon’u düzelt", "Kullanıcıya atanmış geofence_id’yi kontrol et"],
        }
    return {"probable_causes": [], "recommended_actions": []}


def build_base_summary(snap: Dict[str, Any]) -> Dict[str, Any]:
    mp = str(snap.get("mountpoint") or "")
    rovers = snap.get("rover_samples") or []
    users = set()
    ips = set()
    for r in rovers:
        u = r.get("user")
        if u:
            users.add(str(u))
        ip = r.get("client_ip")
        if ip:
            ips.add(str(ip))
    return {
        "mountpoint": mp,
        "source_attached": bool(snap.get("source_attached")),
        "rover_count": _as_int(snap.get("rover_count"), 0),
        "active_users": len(users),
        "active_ips": len(ips),
        "last_rtcmtime_age_s": snap.get("last_rtcmtime_age_s"),
        "rtcm_crc_errors_total": snap.get("rtcm_crc_errors_total"),
        "station_info": snap.get("station_info") or {},
    }


def compute_alerts(snaps: List[Dict[str, Any]], thresholds: Optional[Dict[str, Any]] = None) -> List[Alert]:
    alerts: List[Alert] = []
    thr_global = thresholds or {}
    for snap in snaps:
        mp = str(snap.get("mountpoint") or "")
        if not mp:
            continue

        thr = dict(thr_global)
        if isinstance(snap.get("diagnostics_cfg"), dict):
            thr.update(snap.get("diagnostics_cfg") or {})

        if not bool(snap.get("source_attached")):
            ctx = {}
            ctx.update(_recommendations("NO_SOURCE", ctx))
            alerts.append(Alert(code="NO_SOURCE", severity="critical", message="Base kaynağı bağlı değil", mountpoint=mp, ctx=ctx))
        age = snap.get("last_rtcmtime_age_s")
        rtcm_stale_s = _thr(thr, "rtcm_stale_s", 5.0)
        if age is not None and _as_float(age) > rtcm_stale_s:
            ctx = {"age_s": _as_float(age), "threshold_s": rtcm_stale_s}
            ctx.update(_recommendations("RTCM_STALE", ctx))
            alerts.append(Alert(code="RTCM_STALE", severity="warning", message="RTCM akışı bayat", mountpoint=mp, ctx=ctx))

        si = snap.get("station_info") or {}
        if isinstance(si, dict):
            if not (si.get("antenna_descriptor") or si.get("receiver_descriptor")):
                alerts.append(Alert(code="STATION_META_MISSING", severity="info", message="İstasyon donanım metası yok (1033/1008 bekleniyor)", mountpoint=mp, ctx={"recommended_actions": ["Base’in 1033/1008 mesajı gönderdiğini doğrula", "RTCM message type sayaçlarında 1033/1008 var mı kontrol et"], "probable_causes": ["Base 1033/1008 yayınlamıyor", "RTCM yapılandırması eksik"]}))

        for r in (snap.get("rover_samples") or []):
            conn_id = _as_int(r.get("conn_id"), 0) or None
            user = str(r.get("user") or "") or None
            gga = r.get("gga")
            fixq = None
            nsat = None
            hdop = None
            if isinstance(gga, (list, tuple)) and len(gga) >= 5:
                fixq = _as_int(gga[2], 0)
                nsat = _as_int(gga[3], 0)
                hdop = _as_float(gga[4], 0.0)
            snr = r.get("gsv_snr_mean")
            snr_f = _as_float(snr, -1.0) if snr is not None else None
            last_nmea_age = r.get("last_nmea_age_s")
            nmea_stale_s = _thr(thr, "nmea_stale_s", 10.0)
            if last_nmea_age is not None and _as_float(last_nmea_age) > nmea_stale_s:
                ctx = {"age_s": _as_float(last_nmea_age), "threshold_s": nmea_stale_s}
                ctx.update(_recommendations("NMEA_STALE", ctx))
                alerts.append(Alert(code="NMEA_STALE", severity="info", message="Rover NMEA bayat", mountpoint=mp, conn_id=conn_id, user=user, ctx=ctx))
            if fixq is not None and fixq == 0:
                msg = "Fix yok"
                sev = "warning"
                if nsat is not None and nsat <= 3:
                    msg = "Fix yok (uydu sayısı çok düşük)"
                    sev = "critical"
                snr_low = _thr(thr, "snr_low", 25.0)
                if snr_f is not None and snr_f >= 0 and snr_f < snr_low:
                    msg = "Fix yok (SNR düşük)"
                ctx = {"nsat": nsat, "snr": snr_f, "hdop": hdop, "snr_low": snr_low}
                ctx.update(_recommendations("NO_FIX", ctx))
                alerts.append(Alert(code="NO_FIX", severity=sev, message=msg, mountpoint=mp, conn_id=conn_id, user=user, ctx=ctx))
            if nsat is not None and nsat > 0 and nsat < 10:
                ctx = {"nsat": nsat}
                ctx.update(_recommendations("NSAT_LOW", ctx))
                alerts.append(Alert(code="NSAT_LOW", severity="info", message="Uydu sayısı düşük", mountpoint=mp, conn_id=conn_id, user=user, ctx=ctx))
            snr_low = _thr(thr, "snr_low", 25.0)
            if snr_f is not None and snr_f >= 0 and snr_f < snr_low:
                ctx = {"snr": snr_f, "threshold": snr_low}
                ctx.update(_recommendations("SNR_LOW", ctx))
                alerts.append(Alert(code="SNR_LOW", severity="info", message="Zayıf sinyal (SNR düşük)", mountpoint=mp, conn_id=conn_id, user=user, ctx=ctx))

            if bool(r.get("geofence_violation_recent")):
                ctx = {}
                ctx.update(_recommendations("GEOFENCE_VIOLATION", ctx))
                alerts.append(Alert(code="GEOFENCE_VIOLATION", severity="warning", message="Geofence ihlali (son 60 sn)", mountpoint=mp, conn_id=conn_id, user=user, ctx=ctx))
            if bool(r.get("jamming_suspect_recent")):
                ctx = {}
                ctx.update(_recommendations("JAMMING_SUSPECT", ctx))
                alerts.append(Alert(code="JAMMING_SUSPECT", severity="warning", message="Olası jamming (son 60 sn)", mountpoint=mp, conn_id=conn_id, user=user, ctx=ctx))
            if bool(r.get("spoofing_suspect_recent")):
                ctx = {}
                ctx.update(_recommendations("SPOOFING_SUSPECT", ctx))
                alerts.append(Alert(code="SPOOFING_SUSPECT", severity="warning", message="Olası spoofing (son 60 sn)", mountpoint=mp, conn_id=conn_id, user=user, ctx=ctx))

            ant_suspect = (fixq == 0) and (nsat is not None and nsat <= 3) and (snr_f is not None and snr_f >= 0 and snr_f < 25)
            if ant_suspect:
                ctx = {}
                ctx.update(_recommendations("ANTENNA_OR_RF_SUSPECT", ctx))
                alerts.append(Alert(code="ANTENNA_OR_RF_SUSPECT", severity="warning", message="Anten/RF zinciri şüpheli (no-fix + low nsat + low snr)", mountpoint=mp, conn_id=conn_id, user=user, ctx=ctx))
    return alerts
