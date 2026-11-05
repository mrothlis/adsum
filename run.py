#!/usr/bin/env python3
"""CSU Check-In — Entry point."""

from dotenv import load_dotenv
load_dotenv()  # Must happen before any app imports

import uvicorn

if __name__ == "__main__":
    from app.config import settings

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
