import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv('DATABASE_URL', 'sqlite:///./spa.db')
    secret: str = os.getenv('APP_SECRET_KEY', '')
    admin_username: str = os.getenv('ADMIN_USERNAME', 'admin')
    admin_password: str = os.getenv('ADMIN_PASSWORD', '')
    bark_origin: str = os.getenv('BARK_SERVER_URL', 'https://api.day.app').rstrip('/')
    cookie_secure: bool = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
    worker_enabled: bool = os.getenv('WORKER_ENABLED', 'true').lower() == 'true'
    static_dir: str = os.getenv('STATIC_DIR', 'frontend/dist')
    tle_max_age_days: int = int(os.getenv('TLE_MAX_AGE_DAYS', '14'))


settings = Settings()
