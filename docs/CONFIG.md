Konfigürasyon (config/caster_config.json)

Özet
- Konfig JSON dosyasıyla verilir.
- Sırlar için `env:` öneki desteklenir (ör. `sources.password: "env:SOURCES_PASSWORD"`).
- Hot‑reload açıkken dosya değişikliğiyle konfig otomatik yenilenir ve `env:` sırlar tekrar okunur.

Alanlar ve Varsayılanlar
| Alan | Tip | Varsayılan | Açıklama |
|---|---|---|---|
| `listen.host` | string | `0.0.0.0` | Dinlenecek adres |
| `listen.port` | int | `2101` | TCP port (1–65535) |
| `listen.backlog` | int | `200` | Backlog (>=1) |
| `listen.reuse_port` | bool | `false` | SO_REUSEPORT |
| `listen.tls_certfile` | string? |  | TLS sertifika dosyası |
| `listen.tls_keyfile` | string? |  | TLS key dosyası |
| `listen.tls_client_ca` | string? |  | SOURCE mTLS için CA |
| `logging.level` | string | `INFO` | Log seviyesi |
| `logging.format` | string | `plain` | `plain` / `json` |
| `sourcetable.operator` | string | `NTRIP` | Operator |
| `sourcetable.country` | string | `XX` | Ülke |
| `sourcetable.network` | string | `` | Network |
| `sourcetable.mountpoints_meta` | object | `{}` | Mountpoint meta |
| `sources.password` | string | `` | Source şifresi veya `env:` |
| `sources.mountpoints` | string[] | `[]` | İzinli MP listesi |
| `tiers.<name>` | object |  | Tier tanımı |
| `users.<username>` | object |  | Kullanıcı tanımı |
| `limits.*` | object |  | Limitler |
| `security.*` | object |  | Güvenlik/operasyon |

IoT / Web API Alanları (security)
| Alan | Tip | Varsayılan | Açıklama |
|---|---|---|---|
| `security.api_ws_port` | int | `0` | WebSocket API portu (0=kapalı) |
| `security.iot_mqtt_host` | string | `` | MQTT broker host (boş=kapalı) |
| `security.iot_mqtt_port` | int | `1883` | MQTT port |
| `security.iot_mqtt_username` | string | `` | MQTT kullanıcı adı |
| `security.iot_mqtt_password` | string | `` | MQTT parola |
| `security.iot_mqtt_tls` | bool | `false` | MQTT TLS |
| `security.shadow_redis_url` | string | `` | Device shadow Redis URL (boş=kapalı) |
| `security.shadow_ttl_s` | int | `86400` | Shadow TTL saniye |
| `security.geofence_polygons` | object | `{}` | Geofence tanımları (id→{polygon,mode}) |
| `security.admin_jwt_secret` | string | `` | Admin JWT secret (env destekli) |
| `security.admin_jwt_exp_s` | int | `3600` | JWT geçerlilik süresi |

Diagnostics Alanları (security.diagnostics)
| Alan | Tip | Varsayılan | Açıklama |
|---|---|---|---|
| `security.diagnostics.snr_low` | float | `25` | SNR düşük eşiği |
| `security.diagnostics.nmea_stale_s` | float | `10` | Rover NMEA bayat eşiği (sn) |
| `security.diagnostics.rtcm_stale_s` | float | `5` | RTCM bayat eşiği (sn) |
| `security.diagnostics.jamming_snr` | float | `20` | Jamming heuristiği SNR eşiği |
| `security.diagnostics.jamming_nsat` | int | `8` | Jamming heuristiği NSAT eşiği |
| `security.diagnostics.jamming_hdop` | float | `2.5` | Jamming heuristiği HDOP eşiği |
| `security.diagnostics.spoofing_jump_dist_m` | float | `500` | Spoofing heuristiği mesafe eşiği (m) |
| `security.diagnostics.spoofing_jump_speed_mps` | float | `80` | Spoofing heuristiği hız eşiği (m/s) |

Doğrulama Kuralları
- `listen.port`: 0–65535 (0: test için ephemeral port)
- `listen.backlog`: >= 1
- TLS: `listen.tls_certfile` ve `listen.tls_keyfile` birlikte verilmeli
- `sources.password`: boş olamaz (env çözümlemesi sonrası da)
- `tiers.*`: negatif olamaz
- `users.*`: `password` veya `password_sha256` veya `password_hash` zorunlu
- `users.*.role`: `admin` / `admin_ro` / `geofence_editor` / `user`
- `limits.*`: negatif olamaz
- `security.ip_allow/ip_deny/admin_ip_allow`: IP veya CIDR

Ortam Değişkenleri
- `env:` ile sır alma:
  - `sources.password: "env:SOURCES_PASSWORD"`
  - `security.admin_token: "env:ADMIN_TOKEN"`

Karmaşık Senaryo Örnekleri

1) TLS + SOURCE mTLS
```json
{
  "listen": {
    "host": "0.0.0.0",
    "port": 2101,
    "tls_certfile": "certs/server.crt",
    "tls_keyfile": "certs/server.key",
    "tls_client_ca": "certs/ca.crt"
  },
  "security": { "require_mtls_for_source": true }
}
```

2) Admin allowlist + rate limit + audit sink
```json
{
  "security": {
    "admin_token": "env:ADMIN_TOKEN",
    "admin_ip_allow": ["10.0.0.0/24"],
    "admin_rate_limit_per_min": 60,
    "api_ws_port": 8001,
    "iot_mqtt_host": "mqtt.example.com",
    "iot_mqtt_tls": true,
    "audit_file": "logs/audit.log",
    "audit_max_bytes": 1048576,
    "audit_backups": 3,
    "audit_http_url": "https://audit.example/api/events",
    "audit_http_headers": {"Authorization": "Bearer xxx"}
  }
}
```

## Docker Compose Notları

- Örnek docker config: `config/caster_config.docker.json`
- `env:` alanları compose ortam değişkenlerinden okunur.
- `/api/v1/*` uçları `security.api_ws_port` ile açılan ayrı porttadır (default öneri: 8001).

Konfig Değişiklikleri için Geçiş
- Yeni alanlar varsayılan değerlerle geriye uyumludur.
- Admin CRUD konfig üzerinde atomik güncelleme yapar; konfig dosyasını yedekleyin.
