"""One-time-per-boot setup: a tiny schema migration (no Alembic in this project),
the admin + demo user rows, and a backfill of pre-multi-tenant virtual keys."""

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, VirtualKey
from app.security import hash_password

DEMO_EMAIL = "demo@llm-usage-tracker.local"


def demo_user_id(db: Session) -> int:
    uid = db.scalar(select(User.id).where(User.is_demo.is_(True)))
    if uid is None:  # created by bootstrap(); this is a safety net
        u = User(email=DEMO_EMAIL, password_hash="", is_demo=True)
        db.add(u)
        db.commit()
        uid = u.id
    return uid


def _has_col(db: Session, table: str, col: str) -> bool:
    return bool(
        db.scalar(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": col},
        )
    )


def _has_table(db: Session, table: str) -> bool:
    return bool(
        db.scalar(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
            {"t": table},
        )
    )


def _migrate(db: Session) -> None:
    """No Alembic — a few guarded ALTERs for databases created before a schema bump.
    `Base.metadata.create_all` only creates missing tables, it never alters."""
    # v1: multi-tenancy — virtual_keys.user_id
    if not _has_col(db, "virtual_keys", "user_id"):
        db.execute(text("ALTER TABLE virtual_keys ADD COLUMN user_id BIGINT"))
        db.execute(
            text(
                "ALTER TABLE virtual_keys ADD CONSTRAINT virtual_keys_user_id_fkey "
                "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
            )
        )
        db.execute(
            text("CREATE INDEX IF NOT EXISTS ix_virtual_keys_user_id ON virtual_keys (user_id)")
        )
        db.commit()

    # v2: per-account live mode — drop the short-lived workspaces feature, add
    # users.live_cap_usd, and re-key provider_credentials on user_id.
    if _has_col(db, "users", "workspace_id"):
        # dropping the column also drops its FK to workspaces
        db.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS workspace_id"))
        db.commit()
    if _has_table(db, "workspaces"):
        db.execute(text("DROP TABLE IF EXISTS workspaces CASCADE"))
        db.commit()
    if not _has_col(db, "users", "live_cap_usd"):
        db.execute(text("ALTER TABLE users ADD COLUMN live_cap_usd NUMERIC(12, 6)"))
        db.commit()
    if _has_col(db, "provider_credentials", "workspace_id") or not _has_col(
        db, "provider_credentials", "user_id"
    ):
        # encrypted keys can't be re-keyed; the table is empty in prod — drop + recreate
        db.execute(text("DROP TABLE IF EXISTS provider_credentials"))
        db.commit()
        from app.models import ProviderCredential

        ProviderCredential.__table__.create(bind=db.get_bind(), checkfirst=True)
        db.commit()


def bootstrap(db: Session) -> None:
    _migrate(db)

    # demo account (owns the shared demo dataset; cannot log in)
    demo = db.scalar(select(User).where(User.is_demo.is_(True)))
    if demo is None:
        demo = User(email=DEMO_EMAIL, password_hash="", is_demo=True)
        db.add(demo)
        db.flush()

    # admin account, from env
    admin = db.scalar(select(User).where(User.is_admin.is_(True)))
    email = settings.ADMIN_USERNAME.strip() or "admin"
    pw_hash = hash_password(settings.ADMIN_PASSWORD) if settings.ADMIN_PASSWORD else ""
    if admin is None:
        db.add(User(email=email, password_hash=pw_hash, is_admin=True))
    else:
        admin.email = email
        if settings.ADMIN_PASSWORD:
            admin.password_hash = pw_hash

    # migration: adopt pre-multi-tenant keys into the demo account
    db.execute(
        update(VirtualKey).where(VirtualKey.user_id.is_(None)).values(user_id=demo.id)
    )
    db.commit()
