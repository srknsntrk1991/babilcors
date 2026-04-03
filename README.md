NTRIP Caster Server (Asyncio)

Özet
- Asenkron Python NTRIP Caster: Source (Base) uplink + Rover (Client) downlink fan-out.
- HTTP/ICY uyumlu: `GET /MOUNTPOINT` için `ICY 200 OK`, source için `SOURCE <password> /<mountpoint>`.
- Basic Auth + yetkilendirme: Kullanıcı/mountpoint erişim kontrolü.
- Tier sistemi: Kullanıcıya bağlı hız limiti, epoch limiti ve kuyruk limiti.
- `--check-config`: Konfig doğrulama ve raporlama.
- `/healthz`: Basit sağlık kontrolü.
- Docker: Image build + çalıştırma; healthcheck dahil.

Dokümanlar
- `docs/ARCHITECTURE.md`
- `docs/PROTOCOL.md`
- `docs/CONFIG.md`
- `docs/API.md`
- `docs/DEPLOYMENT.md`
- `docs/SECURITY.md`
- `docs/OPERATIONS.md`

Klasör Yapısı
- README.md
- requirements.txt
- Dockerfile
- .dockerignore
- .gitignore
- main.py
- config/
  - caster_config.json
- docs/
  - ARCHITECTURE.md
  - PROTOCOL.md
  - CONFIG.md
- src/
  - __init__.py
  - caster.py
  - nmea.py
  - auth.py
  - tiers.py
  - sourcetable.py
  - utils.py
- tests/
  - test_auth.py
  - test_tiers.py

Hızlı Başlangıç
- Python 3.11+
- Kurulum:
  - pip install -r requirements.txt
- Konfig:
  - config/caster_config.json dosyasını düzenleyin.
- Çalıştırma:
  - python main.py --config config/caster_config.json
- Kontrol:
  - python main.py --config config/caster_config.json --check-config

Docker (Önerilen)
- Çalıştır:
  - docker compose up --build
- Portlar:
  - Caster/NTRIP: `2101`
  - Web API (WS/SSE/Diagnostics): `8001` (config: `security.api_ws_port`)
- Örnek docker config:
  - `config/caster_config.docker.json`

Tanıtım
- One‑pager: `docs/PROMO_TR.md`
- Neden BabilCORS: `WhyUs.md`

Sağlık Kontrolü
- `GET /healthz` -> `HTTP 200 OK`

Örnek İstemciler
- Rover (HTTP Basic Auth):
  - curl -v http://babil:cors@127.0.0.1:2101/KNY1
- Sourcetable:
  - curl -v http://127.0.0.1:2101/
- Health:
  - curl -v http://127.0.0.1:2101/healthz

Örnek Akışlar
- SOURCE bağlantısı: SOURCE <password> /<mountpoint> satırı ile bağlanır.
- Rover bağlantısı: GET /<mountpoint> HTTP/1.0 isteği, Authorization: Basic ile kullanıcı/şifre doğrulama.

Docker
- docker build -t async-ntrip-caster .
- docker run -p 2101:2101 -v %cd%/config:/app/config async-ntrip-caster

Testler
- python -m unittest discover -s tests -p "test_*.py" -v

Yol Haritası (Uygulandı)
- [x] Source kopunca rover oturumlarını deterministik kapatma
- [x] Mountpoint başına tek source bağlantısı (ikinci source reddedilir)
- [x] Konfig doğrulama (`--check-config` ve runtime öncesi validasyon)
- [x] Rate limit beklemesinde verimli uyku (busy-loop yok)
- [x] `/healthz` endpoint + Docker `HEALTHCHECK`
- [x] Temel unittest’ler (auth + tier)
- [x] Repo hijyeni: `.gitignore` + `__pycache__` temizliği
 - [x] Kullanıcı parola doğrulaması için `password_sha256` ve `env:` desteği
 - [x] Source parolası için `env:` desteği
 - [x] Seçimli JSON log formatı (`logging.format: json`)
 - [x] TLS desteği (opsiyonel `listen.tls_certfile`/`listen.tls_keyfile`)
 - [x] Graceful shutdown: hub’ların toplu kapanışı
 - [x] Hatalı yazımda send loop’un güvenli sonlanması
 - [x] `/metrics` JSON endpoint’i (mountpoint durumları)
 - [x] Bağlantı limitleri: `max_rovers_total`, `max_rovers_per_ip`, `max_sources_total`
 - [x] `source_idle_timeout_s`, `listen.backlog`, `listen.reuse_port`

Yapılacaklar (Endüstriyel)
- Gözlemlenebilirlik
  - [x] Prometheus formatında `/metrics` (counter/gauge)
  - [x] Örnek Grafana dashboard dosyası (`docs/grafana/dashboard.json`)
  - [x] OpenTelemetry uyumlu `traceparent` (connection trace_id) ve korelasyon
  - [x] SLO ve alarm kuralları örneği (`docs/prometheus/rules.yml`)
- Loglama
  - [x] JSON loglarda alanlar: `event`, `mountpoint`, `user`, `client_ip`, `conn_id`
  - [x] Dönen HTTP/ICY yanıtları için `status` ve `latency_ms` (log event: `response`)
- Güvenlik
  - [x] SOURCE bağlantıları için opsiyonel mTLS (TLS açıkken `require_mtls_for_source: true`)
  - [x] IP allow/deny list (security.ip_allow / security.ip_deny)
  - [x] Secret rotation ve konfig hot-reload (SIGHUP + dosya değişikliği izlemesi)
- Protokol/RTCM
  - [x] RTCM frame doğrulama (CRC24Q) ve bozuk paket sayaçları
  - [x] Sourcetable zenginleştirme (format, detaylar, lat/lon, bitrate)
- Dağıtım
  - [x] Docker non-root hardening (app kullanıcısı)
  - [ ] Kapasite düşürme (cap drop) ve read-only fs opsiyonları
  - [ ] GitHub Actions CI (lint+test+docker build) ve Trivy image taraması
  - [x] Kubernetes manifest (Deployment/Service; readiness/liveness)
- Operasyon
  - [x] Admin API (CRUD: users/tiers/mountpoints + enable/disable/kick)
  - [x] Audit log (temel: son N admin işlemi `/admin/audit`)
  - [x] Uzun süreli soak test script’leri (`scripts/soak_source.py`, `scripts/soak_rover.py`)

Adım Adım Yapılanlar
1. Source düşmesi ve tek source kuralını uyguladım.
2. Konfig doğrulamasını genişlettim ve `--check-config` raporlarını zenginleştirdim.
3. Kuyruk gönderimindeki beklemeyi verimli hale getirdim; `/healthz` eklendi ve Docker HEALTHCHECK yazıldı.
4. Temel unittest’leri ekleyip çalıştırdım.
5. Kullanıcı doğrulamasına `password_sha256` ve `env:` desteği ekledim; source parolasına `env:` desteği ekledim.
6. Seçimli JSON log formatını ekledim (`logging.format: json`).
7. TLS desteğini opsiyonel olarak ekledim (`listen.tls_certfile` ve `listen.tls_keyfile`).
8. Graceful shutdown’da tüm hub’ları kapatacak şekilde kapatma yolunu güçlendirdim.
9. Send loop’ta yazım hatasında oturumu güvenli kapatacak kontrol ekledim.
10. `/metrics` JSON endpoint’i ile mountpoint durumlarını dışa açtım.
11. Bağlantı limitleri (toplam, IP başına) ve aktif source limiti ekledim; `source_idle_timeout_s` ile source bekleme süresini sınırladım; `listen.backlog` ve `listen.reuse_port` ayarlarını ekledim.
12. JSON loglara alan bazlı bilgiler eklendi (`event`, `mountpoint`, `user`, `client_ip`, `conn_id`).
13. RTCM 3.2 akışı için frame doğrulama eklendi; metrikler (`rtcm_frames_total`, `rtcm_crc_errors_total`, `rtcm_messages_total{type}`) Prometheus’a yansıtılıyor.
14. Rover’dan gelen NMEA GSA/GSV yakalanıyor (fix type, PDOP/HDOP/VDOP, uydu sayısı, ortalama SNR) ve loglanıyor.

Sourcetable Zenginleştirme
- `docs/CONFIG.md` ile hizalı olarak `sourcetable.mountpoints_meta` kullanabilirsiniz. Örnek:
```
{
  "sourcetable": {
    "operator": "BABILCORS",
    "country": "TR",
    "network": "BABILNET",
    "mountpoints_meta": {
      "KNY1": {
        "identifier": "RTCM32",
        "format": "RTCM 3.2",
        "format_details": "1077(1),1087(1),1097(1),1127(1),1006(10)",
        "carrier": 2,
        "nav_system": "GPS+GLO+GAL+BDS+QZSS+SBAS",
        "latitude": 37.8719,
        "longitude": 32.4846,
        "bitrate": 1200,
        "antenna": "LEIAX1203+GNSS",
        "receiver": "Trimble Alloy",
        "firmware": "v5.3",
        "datum": "ITRF2014"
      }
    }
  }
}
```

Yapılanlar (Log Alanları)
- `logging.format: json` seçildiğinde loglar şu alanları içerir: `event`, `mountpoint`, `user`, `client_ip`, `conn_id`, `rx_bytes_total`, `tx_bytes_total`, `dropped_bytes_total`.
- Örnek satır:
```
{"ts":"2026-04-03T12:00:00","level":"INFO","logger":"caster","msg":"rover_attached","event":"rover_attached","mountpoint":"KNY1","user":"babil","client_ip":"203.0.113.10","conn_id":42}
```

Rover NMEA (420 Kanal)
- 420 kanallı roverlardan gelen uydu görünürlüğü/sinyal bilgisi için NMEA cümleleri yakalanır:
  - `GSA`: fix tipi + PDOP/HDOP/VDOP + kullanılan uydu sayısı
  - `GSV`: görünen uydu sayısı + SNR (ortalama)
- Log event’leri: `rover_gsa`, `rover_gsv`.

Tracing (W3C Trace Context)
- Her bağlantı için bir `trace_id` üretilir ve HTTP/ICY yanıtlara `traceparent` başlığı olarak eklenir.
- Örnek: `traceparent: 00-<32hex-trace_id>-<16hex-span_id>-01`

Admin API
- Authorization: `Authorization: Bearer <admin_token>`
- `GET /admin/status`
- `POST /admin/audit` (JSON: `{ "limit": 100 }`)
- `GET /admin/openapi.json`

Not
- `/api/v1/*` uçları `security.api_ws_port` ile açılan ayrı porttadır.

JWT Admin Akışı
- Login: `POST /admin/login` → `access` + `refresh`
- Refresh: `POST /admin/token/refresh`
- Revoke: `POST /admin/token/revoke`
- Rol: `admin_ro` sadece `GET` çağırabilir

RESTful Admin CRUD
- Users
  - `GET /admin/users` | `GET /admin/users/{username}`
  - `POST /admin/users` (JSON body: `username,tier,mountpoints,[password|password_sha256|password_hash]`)
  - `PATCH /admin/users/{username}` (JSON body: alanlar)
  - `DELETE /admin/users/{username}`
- Tiers
  - `GET /admin/tiers` | `GET /admin/tiers/{name}`
  - `POST /admin/tiers` (JSON body: `name,rate_limit_bps,max_epochs_per_minute,max_queue_bytes`)
  - `PATCH /admin/tiers/{name}`
  - `DELETE /admin/tiers/{name}`
- Mountpoints
  - `GET /admin/mountpoints`
  - `POST /admin/mountpoints` (JSON body: `name`)
  - `DELETE /admin/mountpoints/{name}`
  - Meta güncelle: `PATCH /admin/mountpoints/{name}/meta` (JSON body: meta anahtarları, örn. `latitude`, `antenna`, `bitrate`)
- Operasyonlar
  - `POST /admin/disable` (JSON: `{ "mountpoint": "KNY1" }`)
  - `POST /admin/enable` (JSON: `{ "mountpoint": "KNY1" }`)
  - `POST /admin/kick` (JSON: `{ "mountpoint": "KNY1", "conn_id": 42 }`)
  - `POST /admin/audit` (JSON: `{ "limit": 100 }`)

Rover Sayfalama
- Rover listesi için yüksek ölçekli endpoint:
  - `GET /admin/rovers?page=1&limit=50&mountpoint=KNY1`

Yeni Öneriler
- Audit log’u kalıcılaştırma: dosya/DB (rotasyon + arama) ve “kim hangi token ile” bilgisini ekleme.
- Admin API’yi `POST` + JSON body’ye taşıma (GET yerine) ve idempotent davranış. [Uygulandı]
- Konfig sürümleme: her değişiklikte `config_version` arttırma ve rollback.
- Rate limit/abuse koruması: admin endpoint’lerine ayrı limit ve IP allowlist.

Soak / Load Test
- Source: `python scripts/soak_source.py --mountpoint KNY1 --password sourcepass --secs 60`
- Rover: `python scripts/soak_rover.py --mountpoint KNY1 --user demo --password demo --conns 50 --secs 60`

Prometheus Alert Rules
- Örnek kurallar: `docs/prometheus/rules.yml`

Freshness (Veri Tazeliği)
- Kaynak tazeliği (mountpoint): `caster_mountpoint_last_rtcmtime_age_seconds`
- Rover NMEA yaşı (örnekleme): `caster_rover_last_nmea_age_seconds{mountpoint,conn_id}`
- NMEA ↔ RTCM delta (örnekleme): `caster_rover_nmea_to_rtcmtime_delta_seconds{mountpoint,conn_id}`

Sağlık ve Diagnostics
- `GET /api/v1/health`
- `GET /api/v1/bases`
- `GET /api/v1/alerts`
- `GET /api/v1/events`

IoT Stream
- `WS /api/v1/ws/{mountpoint}`
- `GET /api/v1/stream/{mountpoint}`

Device Shadow
- `GET /api/v1/devices/{device_id}/shadow`
- `GET /api/v1/devices?query=&limit=`
- `GET /api/v1/devices/{device_id}/history?start_ms=&end_ms=&limit=&reverse=`

Grafana Dashboard
- Örnek dashboard: `docs/grafana/dashboard.json`

Kubernetes
- Deployment: `k8s/deployment.yaml`
- Service: `k8s/service.yaml`

Helm Chart
- Yerel kurulum:
  - `helm install babilcors ./helm/babilcors --set image.repository=ghcr.io/<owner>/babilcors-caster --set image.tag=<tag>`

CI/CD
- GH Actions: `.github/workflows/ci.yml` (test + docker build + Trivy)
- GHCR publish: `.github/workflows/release-ghcr.yml` (tag push → ghcr.io/<owner>/babilcors-caster)

Diyagram Render (Mermaid)
- Kaynaklar: `docs/diagrams/*.mmd`
- PNG üretimi (Node gerekli):
  - `node scripts/render_mermaid.mjs`

Admin Güvenlik ve Oran Sınırı
- Allowlist: `security.admin_ip_allow` (CIDR listesi)
- Rate limit: `security.admin_rate_limit_per_min` (IP başına)
- Audit rotasyonu: `security.audit_file`, `security.audit_max_bytes`, `security.audit_backups`
- Harici audit sink’leri (opsiyonel): `security.audit_http_url`, `security.audit_loki_url`, `security.audit_http_headers`

Hot-Reload ve Secret Rotation
- SIGHUP gönderildiğinde veya konfig dosyası değiştiğinde caster, konfigürasyonu yeniden yükler.
- `security.admin_token` ve `sources.password` gibi sırlar `env:` ile veriliyorsa, ortam değiştiğinde tekrar okunur.
- Windows ortamlarında SIGHUP desteklenmeyebilir; dosya değişikliği izleme ile yenileme yapılır.

Yeni Konfig Opsiyonları
```
{
  "logging": { "level": "INFO", "format": "json" },
  "listen": {
    "host": "0.0.0.0",
    "port": 2101,
    "backlog": 200,
    "reuse_port": false,
    "tls_certfile": "certs/server.crt",
    "tls_keyfile": "certs/server.key"
  },
  "sources": { "password": "env:SOURCES_PASSWORD", "mountpoints": ["KNY1"] },
  "limits": {
    "max_rovers_total": 500,
    "max_rovers_per_ip": 5,
    "max_sources_total": 10,
    "source_idle_timeout_s": 60
  },
  "security": {
    "ip_allow": ["10.0.0.0/24", "203.0.113.5"],
    "ip_deny": ["10.0.0.128/25"]
  },
  "users": {
    "babil": { "password_hash": "pbkdf2_sha256$...", "tier": "pro", "mountpoints": ["*"] }
  }
}
```

`password_hash` (önerilen) üretmek için:
```
python - <<'PY'
from src.auth import make_password_hash
print(make_password_hash("parolaniz"))
PY
```

Legacy `password_sha256` üretmek için:
```
python - <<'PY'
import hashlib
print(hashlib.sha256(b"parolaniz").hexdigest())
PY
```

`/metrics` için:
```
curl -v http://127.0.0.1:2101/metrics
```

Prometheus
- Prometheus formatı: `GET /metrics`
- JSON formatı (debug): `GET /metrics.json`

TLS’yi etkinleştirmek için `listen.tls_certfile` ve `listen.tls_keyfile` alanlarını doldurun veya bir ters vekil (nginx/caddy) arkasından çalıştırın.

Notlar
- Varsayılan olarak her 30 saniyede bir mountpoint durum log’u basılır (`mp_status`).
- `requirements.txt` şu an boş tutulabilir; proje büyüdükçe bağımlılıkları buraya ekleyin.
