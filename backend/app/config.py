"""
config.py -- Centralized Application Configuration
====================================================

This module defines all configurable settings for the IMAMU Course Scheduler
backend using Pydantic's BaseSettings class. Pydantic Settings provides:

- Automatic type validation (e.g., ensuring ACCESS_TOKEN_EXPIRE_MINUTES is int).
- Environment variable overrides: any setting can be overridden by setting an
  environment variable with the same name (e.g., export SECRET_KEY=my-secret).
- Sensible defaults for local development so the app runs out of the box.

Settings defined here:
    DATABASE_URL               -- SQLAlchemy connection string (default: local SQLite file)
    SECRET_KEY                 -- HMAC key used to sign/verify JWT tokens
    ALGORITHM                  -- JWT signing algorithm (HS256 = HMAC-SHA256)
    ACCESS_TOKEN_EXPIRE_MINUTES -- How long a JWT token remains valid (default: 24 hours)
    UNIVERSITY_XML             -- Filename of the XML seed data for courses/rooms/etc.

Usage:
    from app.config import settings
    print(settings.DATABASE_URL)

Architecture note:
    A single 'settings' instance is created at module level and imported
    wherever configuration values are needed, ensuring a single source of truth.
"""

import os
from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Base Directory Resolution
# ---------------------------------------------------------------------------
# _base_dir points to the 'backend/' directory (one level above app/).
# This is used to construct the default SQLite database path so that the
# .db file is created inside the backend/ folder regardless of the working
# directory from which the server is launched.
_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BaseSettings):
    """
    Application settings container.

    Each attribute can be overridden by an environment variable of the same
    name. For example, setting DATABASE_URL in the environment will override
    the default SQLite path defined here.

    Attributes:
        DATABASE_URL:  Database connection string. Defaults to a local SQLite
                       file named 'course_scheduler.db' inside the backend/
                       directory. For production, this should point to
                       PostgreSQL or another production-grade RDBMS.

        SECRET_KEY:    Secret key used for signing JWT access tokens.
                       IMPORTANT: Change this to a strong, random value in
                       production to prevent token forgery.

        ALGORITHM:     The cryptographic algorithm used for JWT signing.
                       HS256 (HMAC with SHA-256) is a symmetric algorithm --
                       the same key is used for both signing and verification.

        ACCESS_TOKEN_EXPIRE_MINUTES:
                       Token lifetime in minutes. Default is 1440 (24 hours).
                       After expiry, the user must log in again to obtain a
                       fresh token.

        UNIVERSITY_XML:
                       Filename (or relative path) of the XML file containing
                       the university's seed data (departments, courses, rooms,
                       instructors, time slots, etc.). This file is parsed
                       once at startup to populate an empty database.
    """

    # SQLite connection string -- includes the full path to the .db file
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(_base_dir, 'course_scheduler.db')}"
    )

    # JWT signing key -- must be kept secret; change in production!
    SECRET_KEY: str = os.getenv("SECRET_KEY", "imamu-course-scheduler-secret-key-change-in-production")

    # JWT algorithm -- HS256 is the default symmetric HMAC algorithm
    ALGORITHM: str = "HS256"

    # Token expiry -- 1440 minutes = 24 hours
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Path to the university XML seed file (relative to backend/ or CWD)
    UNIVERSITY_XML: str = os.getenv("UNIVERSITY_XML", "university.xml")


# ---------------------------------------------------------------------------
# Singleton Settings Instance
# ---------------------------------------------------------------------------
# Instantiate once at module level. All other modules import this object
# rather than creating their own Settings instance, ensuring consistency.
settings = Settings()
