# Why BabilCORS?

Bu doküman TUSAGA-Aktif’i “alternatif” gibi konumlandırmak yerine, **TUSAGA-Aktif’in neden değerli ve kritik bir teknoloji olduğunu** ve BabilCORS’un hangi alanlarda **farklı** bir yaklaşım sunduğunu açıklar.

## TUSAGA-Aktif neden değerlidir?
TUSAGA-Aktif, Türkiye’de GNSS tabanlı hassas konumlandırma ekosisteminin temel taşlarından biridir.

- Ulusal ölçekte **referans istasyon ağı** ve standartlaşmış servis yaklaşımı sağlar.
- Kapsama ve süreklilik hedefiyle, kurumların tek tek kurmasının zor olduğu **operasyonel olgunluğu** (saha bakımı, erişilebilirlik, kapasite yönetimi) temsil eder.
- RTK/PPK gibi hassas konumlandırma senaryolarında, sahadaki cihazların güvenilir biçimde düzeltme verisine erişmesini sağlayan bir **altyapı katmanı** sunar.
- Kurumsal ve akademik kullanım için ortak bir zemin oluşturur; ekosistemdeki üretici/istemci çeşitliliğiyle çalışabilen bir **entegrasyon standardı** etkisi yaratır.

## BabilCORS’un farkı (kısa)
BabilCORS, RTCM/NTRIP dağıtımını bir “caster”ın ötesine taşıyarak **IoT gateway + diagnostics + policy enforcement** (geofence/shadow) yetenekleriyle; kurum içi paneller, entegrasyonlar ve operasyon ekipleri için **self‑service** bir işletim katmanı sunar.

## BabilCORS’un Temel Fikri
**Tek bir kaynaktan (sabit istasyon / ağ) gelen RTCM 3.2 düzeltme verisini**, farklı tüketicilere (rover, web dashboard, IoT cihazları, mesaj broker) aynı anda dağıtan, gözlemlenebilirliği yüksek ve operasyonel olarak yönetilebilir bir “Universal IoT Gateway + NTRIP caster” katmanı.

## Neden sadece “aktif bir ulusal servis” yetmeyebilir?
Ulusal servisler güçlüdür; ancak bazı kurum ihtiyaçları doğrudan “servis” ile çözülemez:

- **On-prem / kapalı ağ**: İnternet erişimi olmayan (veya kısıtlı) sahalar, askeri/enerji altyapıları, özel ağlar.
- **Kurumsal erişim politikaları**: Kendi kullanıcı/tier yönetimi, IP allowlist, rate limit, audit ve uyumluluk gereksinimleri.
- **Cihaz çeşitliliği**: NTRIP bilmeyen IoT cihazları, tarayıcı/mobil istemciler (WebSocket/SSE).
- **Telemetri ve “veri tazeliği”**: “RTCM kaç saniye gecikiyor?”, “rover en son ne zaman fix gönderdi?” gibi SLO/SLA odaklı ölçümler.
- **Entegrasyon**: MQTT/Loki/SIEM/Grafana/Prometheus gibi kurum standardı araçlara doğrudan akış.
- **Operasyonel kontrol**: Mountpoint disable/enable, rover kick, hızlı konfig hot-reload, audit trail.

## BabilCORS ile Gelen Yenilikler
Bu repo özelinde hâlihazırda uygulanmış (ve dokümante edilmiş) başlıca yenilikler:

TUSAGA-Aktif’in değeri
- Ulusal ölçekte standartlaşma, yaygın kapsama ve referans altyapı sunar.
- Tekil kurumların tek başına kurmasının zor olduğu operasyonel süreklilik ve erişilebilirlik avantajı sağlar.

BabilCORS’un getirdiği yenilikler
- Aynı RTCM akışını NTRIP’in yanında **MQTT + WebSocket + SSE** ile dağıtır.
- “Neden fix alamıyorum?” sorusuna panelden yanıt veren **Diagnostics/Alerts/Events** üretir; her uyarı için `probable_causes` ve `recommended_actions` döner.
- **Device Shadow + History** ile çevrimdışı cihazın son durumunu ve konum geçmişini saklar.
- **Geofence** (polygon + GeoJSON MultiPolygon) ile saha politika/uyum senaryolarını uygular.
- Heuristik **jamming/spoofing** uyarıları ile erken risk görünürlüğü sağlar.

### 1) Multi-Channel Dağıtım: NTRIP + MQTT + WebSocket
- **NTRIP caster**: Rover’lar için klasik `GET /{mountpoint}` akışı.
- **MQTT publisher**: Her RTCM frame, Protobuf zarfıyla `gnss/v1/{mountpoint}/stream` topic’ine basılır.
- **WebSocket API**: `ws://<host>:<api_ws_port>/api/v1/ws/{mountpoint}` üzerinden aynı Protobuf paketleri tarayıcı/mobil istemcilere aktarılır.
 - **SSE**: `GET /api/v1/stream/{mountpoint}` ile web tarafında basit abonelik.

### 2) RTCM 3.2 “akıllı” işleme (quality & station intelligence)
- CRC24Q doğrulama ve mesaj tipi sayımları.
- 1005/1006 ile **ECEF→WGS84** dönüşümü: istasyon konumu harita üzerinde gösterilebilir.
- 1033/1007/1008 ile **anten/alıcı tanımı** (anten tipi, receiver versiyon/seri gibi alanlar, best-effort).

### 3) Rover tarafında maksimum telemetri (best-effort)
- NMEA: GGA/GSA/GSV (seri birleştirme), ayrıca RMC/VTG/ZDA.
- “Freshness” metrikleri:
  - Mountpoint için `last_rtcmtime_age_seconds`
  - Rover için `last_nmea_age_seconds` ve `nmea_to_rtcmtime_delta_seconds` (örnekleme ile)

### 3.1) Connectivity Logs & Debugging (Self-Service)
- Panel, “Neden fix alamıyorum?” sorusunu debug event/alert olarak gösterebilir:
  - `NO_FIX`, `SNR_LOW`, `RTCM_STALE`, `NO_SOURCE`, `GEOFENCE_VIOLATION`
  - Heuristik: `ANTENNA_OR_RF_SUSPECT`, `JAMMING_SUSPECT`, `SPOOFING_SUSPECT`
- Her uyarı `probable_causes` ve `recommended_actions` ile gelir.

### 4) Endüstriyel gözlemlenebilirlik
- Prometheus `/metrics` + örnek alarm kuralları + örnek Grafana dashboard.
- Structured JSON loglar + `traceparent` korelasyonu.
 - Diagnostics API: `/api/v1/health`, `/api/v1/bases`, `/api/v1/alerts`, `/api/v1/events`.

### 5) Operasyonel yönetim ve güvenlik
- Admin API (RESTful) + audit (dosya rotasyonu + opsiyonel HTTP/Loki sink).
- JWT login/refresh/revoke + rol tabanlı erişim (admin, admin_ro, geofence_editor).
- Admin IP allowlist + per-IP rate limit.
- TLS ve SOURCE için opsiyonel mTLS.
- Konfig hot-reload + `env:` secret rotation.

### 6) Dağıtım kolaylığı
- Docker non-root.
- Kubernetes manifestleri + Helm chart.
- CI: GitHub Actions + Trivy image scan.

## Ne zaman TUSAGA-Aktif, ne zaman BabilCORS?

### TUSAGA-Aktif daha doğru tercih olabilir
- Ulusal referans ağı kapsaması ve standardizasyon hedefleniyorsa.
- Kurum dışı saha cihazları için “hazır servis” kullanımı öncelikliyse.

### BabilCORS daha doğru tercih olabilir
- Kendi istasyonlarınız/özel ağınız var ve **kurum içi dağıtım** yapmak istiyorsanız.
- IoT cihazlarını MQTT ile beslemek, web dashboard’lara WebSocket/SSE sunmak istiyorsanız.
- “Veri tazeliği / kalite / audit / SLO” gibi operasyonel metrikleri zorunlu görüyorsanız.
- SIEM/Loki/Prometheus/Grafana ile kurumsal standarda uyum istiyorsanız.

Kısa konumlandırma
- BabilCORS, “servis tüketmek” yerine **kendi CORS ağını işletmek** isteyen kurumlar içindir.
- Bu yaklaşım, veri/erişim politikalarını ve entegrasyonları kurum içinde standartlaştırır.

## Kısa Özet (One-liner)
**BabilCORS**, RTK düzeltme verisini sadece “caster” olarak değil, **kurum entegrasyonlarına uygun bir Universal IoT Gateway** olarak dağıtır; telemetri, güvenlik ve operasyonel kontrolü birinci sınıf vatandaş yapar.
