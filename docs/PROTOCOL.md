Protokol Özeti (NTRIP v1/v2 uyumlu pragmatik alt küme)

Rover (Client) -> Caster

- İstek:
  - GET /MOUNTPOINT HTTP/1.0 veya HTTP/1.1
  - Authorization: Basic base64(user:pass)
  - Ntrip-GGA: $GPGGA,... (opsiyonel)
  - Connection: close/keep-alive (pratikte TCP açık kalır)
- Yanıt:
  - ICY 200 OK\r\n
  - Server: ...\r\n
  - Content-Type: gnss/data\r\n
  - \r\n
  - Ardından sürekli RTCM3 binary akışı.

Source (Base) -> Caster

- İlk satır:
  - SOURCE <password> /<mountpoint>\r\n
- Header’lar (opsiyonel):
  - Source-Agent: ...\r\n
  - \r\n
- Yanıt:
  - ICY 200 OK\r\n\r\n
- Ardından sürekli RTCM3 binary akışı.

Sourcetable

- GET / HTTP/1.0 isteğine temel STR tablosu döner.

## IoT Gateway Akışları

MQTT
- Topic: `gnss/v1/{mountpoint}/stream`
- Payload: Protobuf `RtcmEnvelope` (RTCM bytes + timestamp + station_id + meta)

Web API (WS/SSE)
- Not: `/api/v1/*` uçları `security.api_ws_port` ile açılan ayrı porttadır.
- WebSocket: `WS /api/v1/ws/{mountpoint}` (binary Protobuf)
- SSE: `GET /api/v1/stream/{mountpoint}` (Base64 Protobuf event)

## Hata Kodları
- 401 Unauthorized: Basic Auth eksik/yanlış.
- 403 Forbidden: Erişim yetkisi yok, limit/allowlist ihlali, mountpoint devre dışı.
- 409 Conflict: Admin CRUD sırasında var olan kaynak oluşturma girişimi.
- 404 Not Found: Admin CRUD sırasında bulunamayan kaynak.
- 429 Too Many Requests: Admin uçlarında oran sınırı aşıldı.

## Timeoutlar
- İlk istek satırı: 5 s
- HTTP header okuma: satır başına 5 s, toplam 64 KiB sınırı
- Rover read loop: 60 s satır bekleme (NMEA)
- Source idle: `limits.source_idle_timeout_s` (0=devre dışı)

## Retry
- Source bağlantısı kesilirse tekrar bağlanmalıdır; exponential backoff önerilir (örn. 1s → 2s → 4s ... max 30s).
- Rover istemcileri TCP kesilmesinde yeniden bağlanmalıdır.

## Sıkıştırma
- RTCM akışı üzerinde ek sıkıştırma uygulanmaz; iletim `gnss/data` binary.
- Docker/K8s dağıtımında TLS sonlandırıcı sıkıştırmayı kapatmalıdır.

## Uyum Matrisi
| İstemci | Protokol | Durum |
|---|---|---|
| NTRIP v1 Rover | HTTP/1.0 | Uyumlu |
| NTRIP v2 Rover | HTTP/1.1 | Uyumlu |
| Source v1 | SOURCE satırı | Uyumlu |
| Sourcetable | NTRIP STR | Uyumlu |
