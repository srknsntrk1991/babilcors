Dağıtım Kılavuzu

## Docker
- Build: `docker build -t babilcors-caster:latest .`
- Çalıştırma: `docker run -p 2101:2101 -v $(pwd)/config:/app/config babilcors-caster:latest`
- Non-root kullanıcı: Dockerfile `app` kullanıcısıyla çalışır.

Docker Compose (önerilen)
- `docker compose up --build`
- Portlar:
  - `2101`: NTRIP/caster + `/admin/*`
  - `8001`: `/api/v1/*` (WS/SSE/Diagnostics) — `security.api_ws_port`
- Örnek config: `config/caster_config.docker.json`

## Kubernetes
- Uygula: `kubectl apply -f k8s/`
- Manifests: `k8s/deployment.yaml`, `k8s/service.yaml`
- Readiness/Liveness: `/healthz`

## Helm
- Chart: `helm/babilcors`
- Kurulum:
  - `helm install babilcors ./helm/babilcors --set image.repository=ghcr.io/<owner>/babilcors-caster --set image.tag=<tag>`
- Konfig: `values.yaml` içindeki `config.caster_config_json` ConfigMap’e yazılır.

## Systemd
```
[Unit]
Description=BabilCORS NTRIP Caster
After=network.target

[Service]
Type=simple
User=app
WorkingDirectory=/opt/babilcors
ExecStart=/usr/bin/python3 main.py --config config/caster_config.json
Restart=always

[Install]
WantedBy=multi-user.target
```

## Monitoring
- Prometheus target: `http://host:2101/metrics`
- Dashboard: `docs/grafana/dashboard.json`
- Alert kuralları: `docs/prometheus/rules.yml`

## Yedekleme/Restore
- Konfig dosyası ve audit logları düzenli yedeklenmelidir.
- Admin CRUD değişiklikleri sonrası konfig dosyasının versiyonlanması önerilir.
