from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class DBConfig:
    dbname: str
    user: str
    password: str
    host: str
    port: int


def load_env() -> None:
    """Load .env into environment variables."""
    load_dotenv()


def get_db_config() -> DBConfig:
    """Read DB config from environment variables."""
    return DBConfig(
        dbname=os.getenv("POSTGRES_DB", "pbl"),
        user=os.getenv("POSTGRES_USER", "pbl"),
        password=os.getenv("POSTGRES_PASSWORD", "1234"),
        host=os.getenv("POSTGRES_HOST", "api.insa-exhibition.shop"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
    )
