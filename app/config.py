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


PLACEHOLDER_SECRET = "change-me-to-a-random-string-at-least-32-chars"


@dataclass
class Settings:
    # No usable default: the app refuses to start in production without a real
    # one. It signs session cookies AND QR check-in tokens, so a shared or
    # guessable value lets anyone forge both.
    SECRET_KEY: str = PLACEHOLDER_SECRET
    DATABASE_URL: str = "sqlite+aiosqlite:///./csu_checkin_v2.db"
    # Deliberately empty. A default password would ship an open admin account
    # to every install that forgot to set one.
    ADMIN_PASSWORD: str = ""
    ADMIN_EMAIL: str = "admin@example.edu"
    SEED_DEMO: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    # Safe by default: DEBUG leaks tracebacks to users.
    DEBUG: bool = False
    QR_ROTATION_SECONDS: int = 20
    QR_TOKEN_EXPIRY_SECONDS: int = 30
    # Only the starting position of the map picker, not a real classroom.
    # Each course sets its own geofence in the UI.
    DEFAULT_GEOFENCE_LAT: float = 0.0
    DEFAULT_GEOFENCE_LON: float = 0.0
    DEFAULT_GEOFENCE_RADIUS_M: float = 75.0
    LATE_THRESHOLD_MINUTES: int = 10
    CHECKIN_WINDOW_MINUTES: int = 15

    # Timestamps are stored as naive UTC; this is the zone they're DISPLAYED in.
    DISPLAY_TIMEZONE: str = os.getenv("DISPLAY_TIMEZONE", "America/New_York")
    # Fallback offset used only if the IANA tz database is missing.
    DISPLAY_UTC_OFFSET_HOURS: int = int(os.getenv("DISPLAY_UTC_OFFSET_HOURS", "-5"))

    # Identity of the deploying institution. Set these in .env; the defaults
    # are deliberately generic so a fresh clone belongs to nobody.
    INSTITUTION_NAME: str = os.getenv("INSTITUTION_NAME", "Your Institution")
    D2L_HOST: str = os.getenv("D2L_HOST", "")
    LTI_CONSUMER_KEY: str = os.getenv("LTI_CONSUMER_KEY", "attendance-checkin")

    # Free unexcused absences before attendance percentage is affected.
    ALLOWED_UNEXCUSED_ABSENCES: int = int(os.getenv("ALLOWED_UNEXCUSED_ABSENCES", "3"))

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
        self._validate()

    def _validate(self) -> None:
        """Fail loudly at startup rather than quietly running insecurely.

        These are the two settings that are dangerous to leave at their
        defaults, and both are easy to miss because the app still appears to
        work. Only enforced outside DEBUG so local experimentation is easy.
        """
        if self.DEBUG:
            return
        problems = []
        if self.SECRET_KEY == PLACEHOLDER_SECRET or len(self.SECRET_KEY) < 32:
            problems.append(
                "SECRET_KEY is unset, still the placeholder, or shorter than 32 "
                "characters. Generate one with:\n"
                "    python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if not self.ADMIN_PASSWORD:
            problems.append("ADMIN_PASSWORD is empty; set it in .env.")
        if problems:
            raise RuntimeError(
                "Refusing to start with an insecure configuration:\n  - "
                + "\n  - ".join(problems)
                + "\n\nSet these in .env (see .env.example), or set DEBUG=true "
                  "for local development only."
            )


settings = Settings()
