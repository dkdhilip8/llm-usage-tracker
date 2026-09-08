"""One-time-per-boot schema migration (no Alembic in this project).

`Base.metadata.create_all` (run just before this) creates any missing *tables*;
these guarded ALTERs bring pre-existing tables up to the current column set.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session


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
    # v1: multi-tenancy — virtual_keys.user_id (skipped once v6 has re-anchored on workspace_id)
    if not _has_col(db, "virtual_keys", "user_id") and not _has_col(
        db, "virtual_keys", "workspace_id"
    ):
        db.execute(text("ALTER TABLE virtual_keys ADD COLUMN user_id BIGINT"))
        db.execute(
            text(
                "ALTER TABLE virtual_keys ADD CONSTRAINT virtual_keys_user_id_fkey "
                "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
            )
        )
        db.commit()

    # v3: drop the demo account
    if _has_col(db, "users", "is_demo"):
        db.execute(text("DELETE FROM users WHERE is_demo = true"))
        db.commit()
        db.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS is_demo"))
        db.commit()

    # v4: per-provider live-spend caps
    if _has_col(db, "provider_credentials", "user_id") and not _has_col(
        db, "provider_credentials", "monthly_cap_usd"
    ):
        db.execute(
            text("ALTER TABLE provider_credentials ADD COLUMN monthly_cap_usd NUMERIC(12, 6)")
        )
        db.commit()
    if _has_col(db, "users", "live_cap_usd"):
        db.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS live_cap_usd"))
        db.commit()

    # v5: username instead of email
    if _has_col(db, "users", "email") and not _has_col(db, "users", "username"):
        db.execute(text("ALTER TABLE users RENAME COLUMN email TO username"))
        db.commit()

    # v6: workspaces as the tenant boundary; the global admin is gone.
    _migrate_v6_workspaces(db)


def _migrate_v6_workspaces(db: Session) -> None:
    """Runs once, guarded by the presence of the old `users.is_admin` column.
    `workspaces` / `workspace_invites` already exist (create_all made them)."""
    if not _has_col(db, "users", "is_admin"):
        return  # already migrated, or a fresh DB built straight from the models

    if not _has_col(db, "users", "workspace_id"):
        db.execute(
            text(
                "ALTER TABLE users ADD COLUMN workspace_id BIGINT "
                "REFERENCES workspaces(id) ON DELETE SET NULL"
            )
        )
    if not _has_col(db, "users", "workspace_role"):
        db.execute(text("ALTER TABLE users ADD COLUMN workspace_role VARCHAR"))
    if not _has_col(db, "virtual_keys", "workspace_id"):
        db.execute(text("ALTER TABLE virtual_keys ADD COLUMN workspace_id BIGINT"))
    if not _has_col(db, "virtual_keys", "assigned_user_id"):
        db.execute(
            text(
                "ALTER TABLE virtual_keys ADD COLUMN assigned_user_id BIGINT "
                "REFERENCES users(id) ON DELETE SET NULL"
            )
        )
    db.commit()

    # provider_credentials was keyed on user_id — encrypted keys can't be re-keyed
    # to a workspace, so drop + recreate (empty on the deploy).
    if _has_col(db, "provider_credentials", "user_id"):
        db.execute(text("DROP TABLE IF EXISTS provider_credentials CASCADE"))
        db.commit()
        from app.models import ProviderCredential

        ProviderCredential.__table__.create(bind=db.get_bind(), checkfirst=True)
        db.commit()

    # backfill: every non-admin account becomes the admin of its own workspace
    rows = db.execute(
        text("SELECT id, username FROM users WHERE is_admin = false AND workspace_id IS NULL")
    ).all()
    for uid, username in rows:
        ws_id = db.scalar(
            text("INSERT INTO workspaces (name) VALUES (:n) RETURNING id"),
            {"n": f"{username}'s workspace"},
        )
        db.execute(
            text("UPDATE users SET workspace_id = :w, workspace_role = 'admin' WHERE id = :u"),
            {"w": ws_id, "u": uid},
        )
        db.execute(
            text("UPDATE virtual_keys SET workspace_id = :w WHERE user_id = :u"),
            {"w": ws_id, "u": uid},
        )
    db.commit()

    # the seeded global admin and any orphan keys go away
    db.execute(text("DELETE FROM users WHERE is_admin = true"))
    db.execute(text("DELETE FROM virtual_keys WHERE workspace_id IS NULL"))
    db.commit()

    db.execute(text("ALTER TABLE virtual_keys DROP COLUMN IF EXISTS user_id"))
    db.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS is_admin"))
    db.execute(text("ALTER TABLE virtual_keys ALTER COLUMN workspace_id SET NOT NULL"))
    db.execute(
        text(
            "ALTER TABLE virtual_keys ADD CONSTRAINT virtual_keys_workspace_id_fkey "
            "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE"
        )
    )
    db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_virtual_keys_workspace_id "
            "ON virtual_keys (workspace_id)"
        )
    )
    db.commit()


def bootstrap(db: Session) -> None:
    _migrate(db)
