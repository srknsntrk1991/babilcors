Operasyon Rehberi

## Troubleshooting
- 401/403: Kullanıcı/tier/mountpoint ve allowlist’i kontrol edin; admin audit’i inceleyin.
- `Mountpoint Busy`: Aynı mountpoint’te aktif source var.
- Drop artışı: `caster_mountpoint_dropped_bytes_total` ve rate limit ayarlarını gözden geçirin.

## Self‑Service Debugging (Panel)

“Neden fix alamıyorum?” sorusu için:
- `GET /api/v1/alerts` (önerilen)
  - Her alert `probable_causes` + `recommended_actions` taşır.
- `GET /api/v1/events` (son olaylar)
  - Örn: `NO_FIX`, `SNR_LOW`, `GEOFENCE_VIOLATION`, `JAMMING_SUSPECT`, `SPOOFING_SUSPECT`

Hızlı teşhis akışı
- Base bağlı mı? `NO_SOURCE`
- RTCM taze mi? `RTCM_STALE`
- Rover NMEA geliyor mu? `NMEA_STALE`
- SNR düşük mü? `SNR_LOW` → anten/konum/parazit kontrolü
- Anten/RF şüpheli mi? `ANTENNA_OR_RF_SUSPECT`

## Performans Ayarları
- `listen.backlog`, `reuse_port` ile accept kapasitesi
- `tiers.*.max_queue_bytes` ve `rate_limit_bps` dengelemesi
- `/metrics` üzerinden RTCM frame rate ve CRC hatalarını izleyin

## Kapasite Planlama
- Rovers total/saniye başına byte; ağ trafiği ve CPU profili
- TokenBucket limitleri ve mountpoint başına kullanıcı dağılımı

## Felaket Kurtarma
- Konfig ve audit yedekleri
- Docker/K8s rollout stratejisi (rolling update)

## Yükseltme Prosedürleri
- Yeni sürümde `--check-config` çalıştırın
- Admin CRUD değişiklikleri için önce `dry-run` (gelecek sürümde) planlayın
