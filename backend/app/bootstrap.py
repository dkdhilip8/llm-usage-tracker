"""One-time-per-boot setup: a tiny schema migration (no Alembic in this project)
and the admin user row."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User
from app.security import hash_password


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

    # v2: per-account live mode — drop the short-lived workspaces feature and
    # re-key provider_credentials on user_id.
    if _has_col(db, "users", "workspace_id"):
        # dropping the column also drops its FK to workspaces
        db.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS workspace_id"))
        db.commit()
    if _has_table(db, "workspaces"):
        db.execute(text("DROP TABLE IF EXISTS workspaces CASCADE"))
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

    # v3: drop the demo account + its fabricated shared dataset
    if _has_col(db, "users", "is_demo"):
        db.execute(text("DELETE FROM users WHERE is_demo = true"))  # cascades keys -> usage
        db.commit()
        db.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS is_demo"))
        db.commit()

    # v4: per-provider live-spend caps — move the cap off users onto each
    # provider_credentials row.
    if not _has_col(db, "provider_credentials", "monthly_cap_usd"):
        db.execute(
            text("ALTER TABLE provider_credentials ADD COLUMN monthly_cap_usd NUMERIC(12, 6)")
        )
        db.commit()
    if _has_col(db, "users", "live_cap_usd"):
        db.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS live_cap_usd"))
        db.commit()

    # v5: sign up with a username, not an email (no email verification anyway)
    if _has_col(db, "users", "email") and not _has_col(db, "users", "username"):
        db.execute(text("ALTER TABLE users RENAME COLUMN email TO username"))
        db.commit()


def bootstrap(db: Session) -> None:
    _migrate(db)

    # admin account, from env
    admin = db.scalar(select(User).where(User.is_admin.is_(True)))
    username = (settings.ADMIN_USERNAME.strip() or "admin").lower()
    pw_hash = hash_password(settings.ADMIN_PASSWORD) if settings.ADMIN_PASSWORD else ""
    if admin is None:
        db.add(User(username=username, password_hash=pw_hash, is_admin=True))
    else:
        admin.username = username
        if settings.ADMIN_PASSWORD:
            admin.password_hash = pw_hash
    db.commit()
