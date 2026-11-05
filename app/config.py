"""Application configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Ensure .env is loaded even when imported by uvicorn's reloader
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Settings:
    SECRET_KEY: str = "change-me-to-a-random-string-at-least-32-chars"
    DATABASE_URL: str = "sqlite+aiosqlite:///./csu_checkin_v2.db"
    ADMIN_PASSWORD: str = "admin123"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    QR_ROTATION_SECONDS: int = 20
    QR_TOKEN_EXPIRY_SECONDS: int = 30
    DEFAULT_GEOFENCE_LAT: float = 32.4600
    DEFAULT_GEOFENCE_LON: float = -84.9877
    DEFAULT_GEOFENCE_RADIUS_M: float = 75.0
    LATE_THRESHOLD_MINUTES: int = 10
    CHECKIN_WINDOW_MINUTES: int = 15
    BASE_URL: str = "http://localhost:8000"

    def __post_init__(self) -> None:
        for fld_name in self.__dataclass_fields__:
            env_val = os.environ.get(fld_name)
            if env_val is not None:
                fld_type = type(getattr(self, fld_name))
                if fld_type is bool:
                    setattr(self, fld_name, env_val.lower() in ("true", "1", "yes"))
                else:
                    setattr(self, fld_name, fld_type(env_val))


settings = Settings()
