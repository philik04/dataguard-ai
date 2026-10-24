"""Small, explicit connection configuration; no automatic .env loading."""

import os
from dataclasses import dataclass, field

import psycopg


@dataclass(frozen=True)
class DatabaseConfig:
    """Connection fields, with the password excluded from representations."""

    dbname: str
    user: str
    password: str = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 5432

    @classmethod
    def from_environment(cls) -> "DatabaseConfig":
        """Require an explicit database/user/password before opening a connection."""
        required = ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError(f"Missing environment settings: {', '.join(missing)}")
        port = int(os.environ.get("POSTGRES_PORT", "5432"))
        if not 1 <= port <= 65535:
            raise ValueError("POSTGRES_PORT must be between 1 and 65535")
        return cls(
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            port=port,
        )

    def connect(self) -> psycopg.Connection:
        """Use explicit transaction blocks rather than implicit transactions."""
        return psycopg.connect(
            dbname=self.dbname,
            user=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            connect_timeout=5,
            autocommit=True,
        )
