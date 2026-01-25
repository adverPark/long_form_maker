# 프로덕션 서버 배포 가이드

## 1. PostgreSQL 설치 및 설정

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install postgresql postgresql-contrib

# DB 및 사용자 생성
sudo -u postgres psql
```

```sql
CREATE DATABASE longform;
CREATE USER longform WITH PASSWORD 'your-secure-password';
ALTER ROLE longform SET client_encoding TO 'utf8';
ALTER ROLE longform SET default_transaction_isolation TO 'read committed';
ALTER ROLE longform SET timezone TO 'Asia/Seoul';
GRANT ALL PRIVILEGES ON DATABASE longform TO longform;
\q
```

## 2. 프로젝트 설정

```bash
# 프로젝트 클론
git clone https://github.com/adverPark/long_form_maker.git
cd long_form_maker/web

# 가상환경 및 의존성 설치
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# 환경변수 설정
cp .env.example .env
nano .env  # 값 수정
```

### .env 설정 예시
```
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=생성한-시크릿-키
ALLOWED_HOSTS=your-domain.com
DB_NAME=longform
DB_USER=longform
DB_PASSWORD=your-db-password
DB_HOST=localhost
DB_PORT=5432
```

시크릿 키 생성:
```bash
uv run python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

## 3. DB 마이그레이션 및 Static 파일

```bash
# 마이그레이션
uv run python manage.py migrate --settings=config.settings.production

# 관리자 계정
uv run python manage.py createsuperuser --settings=config.settings.production

# Static 파일 수집
uv run python manage.py collectstatic --settings=config.settings.production
```

## 4. Gunicorn 서비스 설정

`/etc/systemd/system/longform.service`:
```ini
[Unit]
Description=Long Form Video Maker
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/path/to/long_form_maker/web
Environment="DJANGO_SETTINGS_MODULE=config.settings.production"
ExecStart=/path/to/long_form_maker/web/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable longform
sudo systemctl start longform
```

## 5. Nginx 설정

`/etc/nginx/sites-available/longform`:
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location /static/ {
        alias /path/to/long_form_maker/web/staticfiles/;
    }

    location /media/ {
        alias /path/to/long_form_maker/web/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # 긴 요청 타임아웃 (영상 생성 등)
        proxy_read_timeout 600s;
        proxy_connect_timeout 600s;
    }

    client_max_body_size 500M;  # 영상 업로드용
}
```

```bash
sudo ln -s /etc/nginx/sites-available/longform /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 6. SSL 인증서 (선택)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## 7. 로그 확인

```bash
# Django 로그
tail -f /path/to/long_form_maker/web/logs/django.log
tail -f /path/to/long_form_maker/web/logs/pipeline.log

# Gunicorn 로그
sudo journalctl -u longform -f

# Nginx 로그
sudo tail -f /var/log/nginx/error.log
```

## 8. 업데이트

```bash
cd /path/to/long_form_maker
git pull
cd web
uv sync
uv run python manage.py migrate --settings=config.settings.production
uv run python manage.py collectstatic --noinput --settings=config.settings.production
sudo systemctl restart longform
```
