import secrets
import hashlib
from typing import Optional

from fastapi import Depends, Request


from .database import get_db
from .errors import APIError

# Role hierarchy: admin > team_lead > developer > viewer
ROLE_HIERARCHY = {"admin": 4, "team_lead": 3, "developer": 2, "viewer": 1}

# Minimum role required per action type
WRITE_ROLES = {"admin", "team_lead"}
READ_ROLES = {"admin", "team_lead", "developer", "viewer"}


def generate_api_key() -> str:
    """Generate a securely random API key."""
    return f"ak_{secrets.token_hex(24)}"


def hash_api_key(key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


def hash_password(password: str) -> str:
    """Hash a password for storage using salted SHA-256."""
    salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}${password}".encode()).hexdigest()
    return f"{salt}${h}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored salted hash."""
    if "$" not in stored_hash:
        return False
    salt, h = stored_hash.split("$", 1)
    return hashlib.sha256(f"{salt}${password}".encode()).hexdigest() == h


def generate_session_token() -> str:
    return f"ses_{secrets.token_hex(32)}"


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise APIError("unauthorized", "missing or invalid Authorization header", 401)
    return auth[7:]


def get_current_user(request: Request) -> dict:
    """FastAPI dependency: extract bearer token (API key or session), return user row."""
    token = _extract_bearer_token(request)

    db = get_db()
    try:
        # Check session tokens first
        if token.startswith("ses_"):
            row = db.execute(
                "SELECT u.* FROM sessions s JOIN users u ON s.user_id = u.id "
                "WHERE s.token=? AND u.is_active=1", (token,)
            ).fetchone()
            if row:
                return dict(row)
        # Fall back to API key
        key_hash = hash_api_key(token)
        user = db.execute("SELECT * FROM users WHERE api_key=? AND is_active=1", (key_hash,)).fetchone()
        if not user:
            raise APIError("unauthorized", "invalid or inactive credentials", 401)
        return dict(user)
    finally:
        db.close()


def require_role(*allowed_roles: str):
    """Returns a dependency that checks the user has one of the allowed roles."""
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed_roles:
            raise APIError("forbidden", f"requires role: {', '.join(allowed_roles)}", 403)
        return user
    return checker


def _get_parent_environment_id(resource_type: str, resource_id: str, db) -> Optional[str]:
    """Walk up the resource hierarchy to find the parent environment id."""
    if resource_type == "cluster":
        row = db.execute(
            "SELECT environment_id FROM clusters WHERE id=? AND status!='deleted'", (resource_id,)
        ).fetchone()
        return row["environment_id"] if row else None
    if resource_type == "namespace":
        row = db.execute(
            "SELECT c.environment_id FROM namespaces n "
            "JOIN clusters c ON n.cluster_id = c.id WHERE n.id=?",
            (resource_id,),
        ).fetchone()
        return row["environment_id"] if row else None
    return None


def _resolve_permission(user_id: str, resource_type: str, resource_id: str, db) -> Optional[dict]:
    """
    Return the most specific permission dict {access, role} for the user on this resource.
    Checks direct permission first, then walks up to the parent environment.
    """
    perm = db.execute(
        "SELECT access, role FROM permissions WHERE user_id=? AND resource_type=? AND resource_id=?",
        (user_id, resource_type, resource_id),
    ).fetchone()
    if perm:
        return dict(perm)

    env_id = _get_parent_environment_id(resource_type, resource_id, db)
    if env_id:
        perm = db.execute(
            "SELECT access, role FROM permissions "
            "WHERE user_id=? AND resource_type='environment' AND resource_id=?",
            (user_id, env_id),
        ).fetchone()
        if perm:
            return dict(perm)

    return None


def check_resource_access(
    user: dict,
    resource_type: str,
    resource_id: str,
    need_write: bool = False,
    need_role: Optional[str] = None,
):
    """
    Check if user has access to a resource.

    - Admins bypass all checks.
    - Walks up the hierarchy: direct permission → parent environment permission.
    - need_write=True  : permission must have access='write'.
    - need_role=<role> : effective role (permission role or global role fallback)
                         must meet or exceed the required role.
    """
    if user["role"] == "admin":
        return

    db = get_db()
    try:
        perm = _resolve_permission(user["id"], resource_type, resource_id, db)

        if not perm:
            raise APIError("forbidden", f"no access to {resource_type} {resource_id}", 403)

        if need_write and perm["access"] != "write":
            raise APIError("forbidden", f"write access required for {resource_type} {resource_id}", 403)

        if need_role:
            # Effective role = permission-level role (if set) else global user role
            effective = perm["role"] if perm.get("role") else user["role"]
            if ROLE_HIERARCHY.get(effective, 0) < ROLE_HIERARCHY.get(need_role, 0):
                raise APIError(
                    "forbidden",
                    f"requires {need_role} role for {resource_type} {resource_id}",
                    403,
                )
    finally:
        db.close()


# Convenience dependencies
require_admin = require_role("admin")
require_team_lead = require_role("admin", "team_lead")
require_developer = require_role("admin", "team_lead", "developer")
require_viewer = require_role("admin", "team_lead", "developer", "viewer")
