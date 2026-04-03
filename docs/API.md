API Referansı

## Admin API

Kimlik Doğrulama
- Header: `Authorization: Bearer <admin_token>`
- IP allowlist: `security.admin_ip_allow` (boşsa serbest)
- Rate limit: `security.admin_rate_limit_per_min` (IP başına)

JWT (Önerilen)
- `POST /admin/login` ile `access`+`refresh` alınır.
- `Authorization: Bearer <access>` ile admin uçlarına erişilir.
- Read-only admin: `role=admin_ro` sadece `GET` uçlarını çağırabilir.
 - Geofence editor: `role=geofence_editor` sadece kendi geofence’lerini yönetebilir.

Örnek curl
- Login:
```bash
curl -s -X POST http://127.0.0.1:2101/admin/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"pass"}'
```
- Refresh:
```bash
curl -s -X POST http://127.0.0.1:2101/admin/token/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh":"<refresh>"}'
```
- Revoke:
```bash
curl -s -X POST http://127.0.0.1:2101/admin/token/revoke \
  -H "Authorization: Bearer <access>" \
  -H "Content-Type: application/json" \
  -d '{"token":"<access>","type":"access"}'
```

- Me:
```bash
curl -s http://127.0.0.1:2101/admin/me \
  -H "Authorization: Bearer <access>"
```

OpenAPI
- `GET /admin/openapi.json`

Genel Hata Formatı
```json
{ "code": "invalid_body", "message": "validation error", "details": "..." }
```

### Users

Liste
- `GET /admin/users`

Detay
- `GET /admin/users/{username}`

Oluştur
- `POST /admin/users`
```json
{
  "username": "demo",
  "tier": "free",
  "mountpoints": ["KNY1"],
  "password": "demo"
}
```

Güncelle
- `PATCH /admin/users/{username}`
```json
{ "tier": "pro", "mountpoints": ["*"] }
```

Rol Güncelle (Admin UI için)
- `PATCH /admin/users/{username}`
```json
{ "role": "admin_ro" }
```

Sil
- `DELETE /admin/users/{username}`

### Tiers

Liste
- `GET /admin/tiers`

Detay
- `GET /admin/tiers/{name}`

Oluştur
- `POST /admin/tiers`
```json
{ "name": "pro", "rate_limit_bps": 0, "max_epochs_per_minute": 0, "max_queue_bytes": 1048576 }
```

Güncelle
- `PATCH /admin/tiers/{name}`
```json
{ "rate_limit_bps": 20000 }
```

Sil
- `DELETE /admin/tiers/{name}`

### Mountpoints

Liste
- `GET /admin/mountpoints`

Ekle
- `POST /admin/mountpoints`
```json
{ "name": "KNY1" }
```

Sil
- `DELETE /admin/mountpoints/{name}`

Meta Güncelle
- `PATCH /admin/mountpoints/{name}/meta`
```json
{ "latitude": 37.8719, "longitude": 32.4846, "antenna": "LEIAX1203+GNSS", "bitrate": 1200 }
```

### Operasyonlar

Status
- `GET /admin/status`

Me
- `GET /admin/me`

Disable
- `POST /admin/disable`
```json
{ "mountpoint": "KNY1" }
```

Enable
- `POST /admin/enable`
```json
{ "mountpoint": "KNY1" }
```

Kick
- `POST /admin/kick`
```json
{ "mountpoint": "KNY1", "conn_id": 42 }
```

Audit Tail
- `POST /admin/audit`
```json
{ "limit": 100 }
```

### Geofences

- `GET /admin/geofences`
- `POST /admin/geofences`
```json
{ "id": "TR_KNY_ZONE1", "mode": "block", "polygon": [[37.87,32.48],[37.90,32.48],[37.90,32.52],[37.87,32.52]] }
```

Notlar
- `role=geofence_editor` kullanıcılar geofence oluşturabilir/güncelleyebilir/silebilir; ancak sadece `owner=<username>` olan geofence’leri yönetebilir.
- Admin, `owner` alanını set edebilir/güncelleyebilir.

- `PATCH /admin/geofences/{id}`
```json
{ "mode": "alert" }
```

- `DELETE /admin/geofences/{id}`

Kullanıcıya Geofence Atama
- `PATCH /admin/users/{username}`
```json
{ "geofence_id": "TR_KNY_ZONE1" }
```

### Rover Listesi (Sayfalama)

- `GET /admin/rovers?page=1&limit=50&mountpoint=KNY1`
- Yanıt:
```json
{
  "page": 1,
  "limit": 50,
  "total": 1234,
  "items": [
    {
      "mountpoint": "KNY1",
      "conn_id": 42,
      "user": "demo",
      "client_ip": "203.0.113.10",
      "sent_bytes": 102400,
      "dropped_bytes": 0,
      "last_nmea_age_s": 0.8,
      "gsv_snr_mean": 33.5,
      "gsv_total_sv": 28
    }
  ]
}
```

## Device Shadow

- `GET /api/v1/devices/{device_id}/shadow`
- Yanıt:
```json
{ "ok": true, "shadow": {"lat": 37.87, "lon": 32.48, "nsat": 18, "mountpoint": "KNY1"} }
```

- Liste/arama:
  - `GET /api/v1/devices?query=demo&limit=100`

## Stream (Web)

Not
- `/api/v1/*` uçları caster portunda değil, `security.api_ws_port` ile açılan ayrı porttadır.

- WebSocket:
  - `WS /api/v1/ws/{mountpoint}` (binary Protobuf `RtcmEnvelope`)
- SSE:
  - `GET /api/v1/stream/{mountpoint}` (Base64 Protobuf event)

## Diagnostics

- `GET /api/v1/health`
- `GET /api/v1/bases` (özet)
- `GET /api/v1/bases?detail=true` (detay)
- `GET /api/v1/bases/{mountpoint}`
- `GET /api/v1/alerts?mountpoint=&user=&severity=`
- `GET /api/v1/events?limit=200`

## Sağlık ve Diagnostics

- Health:
  - `GET /api/v1/health`
- Base (mountpoint) özetleri:
  - `GET /api/v1/bases`
  - `GET /api/v1/bases?detail=true`
  - `GET /api/v1/bases/{mountpoint}`
- Uyarılar:
  - `GET /api/v1/alerts`
  - `GET /api/v1/alerts?mountpoint=KNY1&user=demo&severity=warning`
- Event/Debug log buffer:
  - `GET /api/v1/events?limit=200`

Alert Kodları (örnek)
- `NO_SOURCE`: Base kaynağı bağlı değil
- `RTCM_STALE`: RTCM akışı bayat
- `NO_FIX`: Rover fix yok
- `SNR_LOW`: Zayıf sinyal (SNR düşük)
- `JAMMING_SUSPECT`: Olası jamming (heuristik)
- `SPOOFING_SUSPECT`: Olası spoofing (ani konum sıçraması heuristiği)
- `GEOFENCE_VIOLATION`: Geofence ihlali
- `ANTENNA_OR_RF_SUSPECT`: Anten/RF zinciri şüpheli (heuristik)

Alert ctx Alanları
- `probable_causes`: Olası sebepler listesi
- `recommended_actions`: Operatör için önerilen aksiyonlar
