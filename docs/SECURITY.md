Güvenlik Kılavuzu

## Tehdit Modeli
- Yetkisiz erişim (admin uçları, rover/source auth)
- Trafik dinleme/bozulma (TLS/mTLS)
- Hizmet reddi (aşırı istek, flood)
- Konfig manipülasyonu

## Hardening
- TLS etkinleştirin; SOURCE için mTLS opsiyonunu değerlendirin.
- Admin IP allowlist ve per‑IP rate limit ayarlayın.
- Konteyner: non-root, read-only fs, gerekirse cap drop.
- Secrets: `env:` ve gizli değişken yönetimi; loglara sızıntıyı önleyin.

## JWT ve Roller
- JWT: `POST /admin/login` → `access`+`refresh`, `POST /admin/token/refresh`, `POST /admin/token/revoke`.
- Roller:
  - `admin`: tüm admin write işlemleri
  - `admin_ro`: sadece GET (read-only)
  - `geofence_editor`: sadece kendi geofence’leri (owner) üzerinde write
- Revocation list:
  - Redis varsa `jwtrev:{jti}` anahtarları ile token iptali kalıcı hale gelir.

## Audit Gereksinimleri
- Tüm admin işlemleri audit’e yazılır (token parmak izi, client IP, trace_id).
- Audit’i harici SIEM/Loki’ye gönderin; saklama/erişim politikası belirleyin.

## Uyum/Checklist
- Şifre ilkeleri (min uzunluk, hashlenmiş saklama)
- Ağ sınırları ve firewall kuralları
- Log saklama ve KVKK/GDPR değerlendirmesi
- Yedekleme ve erişim kontrolleri

## Pentest Rehberi
- Auth, rate limit ve allowlist baypas denemeleri
- TLS/mTLS doğrulaması
- Kaynak tüketim testleri (yük, soak, flood)
