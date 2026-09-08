"""Who-can-see-what for the read endpoints. Sign-in AND workspace membership are
required.

Workspace Admin -> every key + usage row in their workspace.
Team Member     -> only the virtual key(s) assigned to them, and their usage.

`scope_usage` is the single tenancy chokepoint for /api/usage/* and /api/requests.
"""

from fastapi import Depends
from sqlalchemy import Select, select

from app.models import UsageLog, VirtualKey
from app.workspace import Membership, require_membership

# `Viewer` is just the membership under a name the read routers already use.
Viewer = Membership


def viewer(m: Membership = Depends(require_membership)) -> Viewer:
    return m


def scope_usage(stmt: Select, v: Viewer) -> Select:
    """Restrict a usage_logs query to what `v` may see."""
    keys = select(VirtualKey.id).where(VirtualKey.workspace_id == v.workspace_id)
    if not v.is_admin:
        keys = keys.where(VirtualKey.assigned_user_id == v.user_id)
    return stmt.where(UsageLog.key_id.in_(keys))


def scope_keys(stmt: Select, v: Viewer) -> Select:
    """Restrict a virtual_keys query to what `v` may see."""
    stmt = stmt.where(VirtualKey.workspace_id == v.workspace_id)
    if not v.is_admin:
        stmt = stmt.where(VirtualKey.assigned_user_id == v.user_id)
    return stmt
