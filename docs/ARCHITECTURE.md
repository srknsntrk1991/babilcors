Mimari

Özet
- Asyncio tabanlı NTRIP caster: Source (Base) akışını mountpoint başına tek kabul eder ve Rover (Client) bağlantılarına fan-out yapar.
- Protokoller: SOURCE akışı (NTRIP) + HTTP/ICY GET /MOUNTPOINT.
- QoS: Tier bazlı rate limit (bps), epoch gating ve rover başına sınırlı kuyruk.
- Gözlemlenebilirlik: Prometheus `/metrics`, JSON log alanları, `traceparent` korelasyonu, audit trail.

Universal IoT Gateway
- MQTT Publisher: RTCM frame’leri `gnss/v1/{mountpoint}/stream` topic’ine Protobuf zarfıyla basılır.
- Web API (FastAPI): `security.api_ws_port` üstünde WS/SSE stream + diagnostics uçları sunulur.
- Device Shadow: Redis üzerinde son bilinen durum + konum geçmişi.
- Diagnostics: health/bases/alerts/events; her alert için önerilen aksiyonlar.

Bileşen Diyagramı (Mermaid)
```mermaid
flowchart LR
  subgraph Clients
    S((Source))
    R1((Rover))
    R2((Rover))
  end

  subgraph Caster
    C[NtripCaster]
    H[MountpointHub]
    A[Auth]
    T[Tiers]
    Q[ByteQueue]
    M[Metrics]
    L[Logs/Audit]
  end

  S -->|RTCM3| C
  R1 -->|GET /MP| C
  R2 -->|GET /MP| C
  C --> H
  C --> A
  C --> T
  H --> Q
  C --> M
  C --> L
  C -->|RTCM3| R1
  C -->|RTCM3| R2
```

Akış (Sequence)
```mermaid
sequenceDiagram
  participant S as Source
  participant C as Caster
  participant H as MountpointHub
  participant R as Rover

  S->>C: SOURCE <pwd> /MP
  C->>H: attach_source(MP)
  C-->>S: ICY 200 OK

  R->>C: GET /MP + Authorization
  C->>H: add_rover(session)
  C-->>R: ICY 200 OK

  loop RTCM stream
    S->>C: RTCM bytes
    C->>C: RTCM 3.2 frame validate + metrics
    C->>H: on_source_data(valid frames)
    H-->>R: enqueue chunk
    R-->>C: NMEA GGA/GSA/GSV (opsiyonel)
  end
```

Dağıtım Diyagramı (Mermaid)
```mermaid
flowchart TB
  Internet((Internet)) --> LB[Load Balancer / Ingress]
  LB --> Pod[Caster Pod]
  Pod -->|/metrics| Prom[Prometheus]
  Prom --> Graf[Grafana]
  Pod --> Loki[Loki/HTTP Audit Sink]
```

Ölçeklenebilirlik
- Bağlantı modeli: Her TCP bağlantı için asyncio task; mountpoint başına `MountpointHub`.
- Backpressure: Rover başına `ByteQueue` (limitli). Queue dolarsa drop artar ve metrik/loglara yansır.
- Rate limiting: TokenBucket ile kullanıcı tier’ına göre bps limiti; CPU verimli bekleme.
- Yatay ölçekleme: Paylaşımsız state. Çoklu instance için mountpoint bazlı L4 hash yönlendirme önerilir.

Performans Karakteristikleri
- Source->caster->rover fan-out ile toplam çıkış bant genişliği rover sayısı ile artar.
- `listen.backlog` ve `reuse_port` accept performansını etkiler.
- RTCM doğrulama: CRC24Q + frame parse; metrikler ile frame rate/CRC error gözlenir.

Hata Modları ve Etkileri
- Source kopması: Hub tüm rover kuyruklarını kapatır; rover bağlantıları deterministik kapanır.
- Auth başarısızlığı: 401/403 döner; unauthorized sayaçları artar.
- Aşırı yük: Queue dolması → dropped_bytes artışı; alarm/monitoring ile yakalanır.
- Admin kötüye kullanım: IP allowlist + rate limit + audit ile korunur.

Güvenlik Mimarisi
- TLS: Opsiyonel; SOURCE için opsiyonel mTLS (CA ile doğrulama + istemci sertifikası zorunluluğu).
- Admin: Bearer token + IP allowlist + per-IP rate limit; tüm admin işlemleri audit’e yazılır.
- Secret rotation: `env:` ile sırlar; hot-reload ile yeniden okunur.
