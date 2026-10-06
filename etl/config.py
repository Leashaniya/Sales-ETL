from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REJECTED_DIR = DATA_DIR / "rejected"
SQL_DIR = PROJECT_ROOT / "sql"

DEFAULT_RAW_FILE = RAW_DIR / "data.csv"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value


@dataclass(frozen=True)
class PostgresSettings:
    host: str = field(default_factory=lambda: _env("PGHOST", "localhost"))
    port: int = field(default_factory=lambda: int(_env("PGPORT", "5432")))
    dbname: str = field(default_factory=lambda: _env("PGDATABASE", "retail_db"))
    user: str = field(default_factory=lambda: _env("PGUSER", "postgres"))
    password: str | None = field(default_factory=lambda: _env("PGPASSWORD") or None)

    def connect_kwargs(self) -> dict:
        kwargs = {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "application_name": "retail_etl_pipeline",
        }
        if self.password:
            kwargs["password"] = self.password
        return kwargs


@dataclass(frozen=True)
class S3Settings:
    bucket: str | None = field(default_factory=lambda: _env("S3_BUCKET_NAME"))
    prefix: str = field(default_factory=lambda: (_env("S3_PREFIX", "retail-etl") or "retail-etl").strip("/"))
    region: str = field(default_factory=lambda: _env("AWS_DEFAULT_REGION", "ap-south-1"))

    @property
    def enabled(self) -> bool:
        return bool(self.bucket)
