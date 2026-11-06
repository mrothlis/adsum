"""Application configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

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
    DATABASE_URL: str = "sqlite+aiosqlite:///./adsum.db"
    # Deliberately empty. A default password would ship an open admin account
    # to every install that forgot to set one.
    ADMIN_PASSWORD: str = ""
    ADMIN_EMAIL: str = "admin@example.edu"
    SEED_DEMO: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    # Safe by default: DEBUG leaks tracebacks to users.
    DEBUG: bool = False
    # A token is valid for its own rotation slot plus the previous one, so the
    # effective lifetime is between one and two times this value. There is no
    # separate expiry setting: a second knob that silently disagreed with the
    # rotation period would be worse than none.
    QR_ROTATION_SECONDS: int = 20
    # Only the starting position of the map picker, not a real classroom.
    # Each course sets its own geofence in the UI.
    DEFAULT_GEOFENCE_LAT: float = 0.0
    DEFAULT_GEOFENCE_LON: float = 0.0
    DEFAULT_GEOFENCE_RADIUS_M: float = 75.0
    # Timestamps are stored as naive UTC; this is the zone they're DISPLAYED in.
    DISPLAY_TIMEZONE: str = os.getenv("DISPLAY_TIMEZONE", "America/New_York")
    # Fallback offset used only if the IANA tz database is missing.
    DISPLAY_UTC_OFFSET_HOURS: int = int(os.getenv("DISPLAY_UTC_OFFSET_HOURS", "-5"))

    # Identity of the deploying institution. Set these in .env; the defaults
    # are deliberately generic so a fresh clone belongs to nobody.
    INSTITUTION_NAME: str = os.getenv("INSTITUTION_NAME", "Your Institution")
    D2L_HOST: str = os.getenv("D2L_HOST", "")
    LTI_CONSUMER_KEY: str = os.getenv("LTI_CONSUMER_KEY", "adsum")

    # NOTE: attendance policy (forgiven absences, late threshold, whether
    # late counts as present) is deliberately NOT here. It is a syllabus
    # decision set per course by the instructor, not something the person who
    # installs the server should dictate. See app/scoring.py.

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
