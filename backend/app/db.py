from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# `pool_pre_ping` + a short connect timeout absorb Neon's scale-to-zero cold wake
# (~300 ms) without surfacing stale-connection errors.
engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 10},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
