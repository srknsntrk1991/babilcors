# BabilCORS – Universal CORS + IoT Gateway

## Tek Cümle (One‑liner)
**BabilCORS**, GNSS/RTK düzeltme verisini (RTCM 3.2) NTRIP’in ötesine taşıyarak **MQTT + WebSocket + SSE** üzerinden dağıtan; **telemetri, güvenlik, geofence, device shadow ve diagnostics** ile “ulusal ölçekte işletilebilir” bir **Universal IoT Gateway** katmanıdır.

## 30 Saniyelik Tanıtım
Sabit istasyonlardan gelen RTCM akışını tek bir noktada toplar, mountpoint bazında rover’lara dağıtır. Aynı veriyi IoT cihazları için MQTT’ye, web/mobil uygulamalar için WebSocket/SSE’ye çevirir. Operasyon ekibi ise panelden “hangi base’de kaç kullanıcı var?”, “RTCM taze mi?”, “SNR düşük mü?”, “jamming/spoofing şüphesi var mı?” gibi sorulara anında yanıt alır.

## Kime Ne Sağlar?

### Operasyon / NOC
- “Neden fix alamıyorum?” sorusunu kullanıcı sormadan önce **alert** olarak gösterir.
- `NO_SOURCE`, `RTCM_STALE`, `NO_FIX`, `SNR_LOW`, `GEOFENCE_VIOLATION`, `JAMMING_SUSPECT`, `SPOOFING_SUSPECT`.
- Her uyarı `probable_causes` + `recommended_actions` ile gelir.

### IoT ve Saha Ekipleri
- NTRIP bilmeyen cihazlar MQTT’ye subscribe olur: `gnss/v1/{mountpoint}/stream`.
- Bağlantı kopsa bile device shadow ile **son bilinen durum** kaybolmaz.

### Yazılım Ekipleri
- Web dashboard’lar `WS /api/v1/ws/{mountpoint}` ile binary Protobuf alır.
- SSE ile minimum entegrasyon maliyeti: `GET /api/v1/stream/{mountpoint}`.

### Güvenlik / Uyumluluk
- JWT login/refresh/revoke, rol tabanlı yetki.
- Audit log + opsiyonel HTTP/Loki sink.
- Admin IP allowlist + rate limit.

## Öne Çıkan Özellikler
- **Multi‑Channel Distribution**: NTRIP + MQTT + WS + SSE
- **Protobuf zarfı**: `RtcmEnvelope` ile self‑describing stream
- **Diagnostics API**: health / bases / alerts / events
- **Geofence**: polygon + GeoJSON MultiPolygon + kullanıcıya özel geofence atama
- **Device Shadow + History**: Redis üzerinde kalıcı durum + son N konum geçmişi
- **Observability**: Prometheus metrikleri + örnek alert kuralları + Grafana dashboard
- **Kubernetes/Helm**: üretime hazır dağıtım

## Demo Senaryosu
“KNY1 base’inde fix alamayan rover” için panel:
- Base bağlı mı? (`NO_SOURCE`)
- RTCM taze mi? (`RTCM_STALE`)
- Rover NMEA geliyor mu? (`NMEA_STALE`)
- SNR/NSAT/HDOP ne durumda? (`SNR_LOW`, `NSAT_LOW`)
- Anten/RF şüpheli mi? (`ANTENNA_OR_RF_SUSPECT`)
- Jamming/spoofing olasılığı? (`JAMMING_SUSPECT`, `SPOOFING_SUSPECT`)

## Hızlı Başlangıç (Docker)
- `docker compose up --build`
- NTRIP: `http://localhost:2101/`
- Admin: `http://localhost:2101/admin/status`
- Web API: `http://localhost:8001/api/v1/health`

