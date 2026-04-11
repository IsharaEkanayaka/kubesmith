import secrets
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..auth import (
    generate_api_key,
    hash_api_key,
    hash_password,
    require_admin,
    check_resource_access,
    get_current_user,
)
from ..database import get_db
from ..errors import APIError
from ..models import (
    CreateUserRequest,
    GrantPermissionRequest,
    PermissionDetail,
    UserDetail,
)
from ..auth import ROLE_HIERARCHY

router = APIRouter()


def _gen_id(prefix: str) -> str:
    chars = string.ascii_lowercase + string.digits
    return f"{prefix}_{''.join(secrets.choice(chars) for _ in range(8))}"


# --- Current user info (any authenticated user) ---

@router.get("/users/me", response_model=UserDetail)
def get_me(user: dict = Depends(get_current_user)):
    return UserDetail(
        id=user["id"], username=user["username"], name=user["name"],
        role=user["role"], is_active=bool(user["is_active"]),
        created_at=user["created_at"],
    )


# --- User management (admin only) ---

@router.post("/users", status_code=201, response_model=UserDetail)
def create_user(req: CreateUserRequest, user: dict = Depends(require_admin)):
    db = get_db()
    try:
        if db.execute("SELECT 1 FROM users WHERE username=?", (req.username,)).fetchone():
            raise APIError("conflict", "username already taken", 409)

        user_id = _gen_id("usr")
        raw_key = generate_api_key()
        key_hash = hash_api_key(raw_key)
        pw_hash = hash_password(req.password)
        now = datetime.now(timezone.utc).isoformat()

        db.execute(
            "INSERT INTO users (id,username,name,role,api_key,password_hash,is_active,created_at) VALUES (?,?,?,?,?,?,1,?)",
            (user_id, req.username, req.name, req.role, key_hash, pw_hash, now),
        )
        db.commit()

        # Return the raw key only on creation — it's hashed in DB
        return UserDetail(
            id=user_id, username=req.username, name=req.name, role=req.role,
            api_key=raw_key, is_active=True, created_at=now,
        )
    finally:
        db.close()


@router.get("/users", response_model=list[UserDetail])
def list_users(user: dict = Depends(require_admin)):
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        return [
            UserDetail(id=r["id"], username=r["username"], name=r["name"], role=r["role"],
                       is_active=bool(r["is_active"]), created_at=r["created_at"])
            for r in rows
        ]
    finally:
        db.close()


@router.get("/users/{user_id}", response_model=UserDetail)
def get_user(user_id: str, user: dict = Depends(require_admin)):
    db = get_db()
    try:
        r = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not r:
            raise APIError("not_found", "user not found", 404)
        return UserDetail(id=r["id"], username=r["username"], name=r["name"], role=r["role"],
                          is_active=bool(r["is_active"]), created_at=r["created_at"])
    finally:
        db.close()


@router.delete("/users/{user_id}", status_code=204)
def deactivate_user(user_id: str, user: dict = Depends(require_admin)):
    if user_id == user["id"]:
        raise APIError("bad_request", "cannot deactivate yourself", 400)
    db = get_db()
    try:
        r = db.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone()
        if not r:
            raise APIError("not_found", "user not found", 404)
        db.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))
        db.commit()
    finally:
        db.close()


# --- Permission management (admin only) ---

@router.post("/permissions", status_code=201, response_model=PermissionDetail)
def grant_permission(req: GrantPermissionRequest, user: dict = Depends(require_admin)):
    db = get_db()
    try:
        target = db.execute("SELECT * FROM users WHERE id=? AND is_active=1", (req.user_id,)).fetchone()
        if not target:
            raise APIError("not_found", "user not found", 404)

        # role is only meaningful for environment permissions
        if req.role and req.resource_type != "environment":
            raise APIError("bad_request", "role can only be set for environment permissions", 400)

        # role cannot exceed the target user's global role ceiling
        if req.role:
            ceiling = ROLE_HIERARCHY.get(target["role"], 0)
            requested = ROLE_HIERARCHY.get(req.role, 0)
            if requested > ceiling:
                raise APIError(
                    "bad_request",
                    f"cannot grant role '{req.role}' — exceeds user's global ceiling '{target['role']}'",
                    400,
                )

        existing = db.execute(
            "SELECT id FROM permissions WHERE user_id=? AND resource_type=? AND resource_id=?",
            (req.user_id, req.resource_type, req.resource_id),
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE permissions SET access=?, role=? WHERE id=?",
                (req.access, req.role, existing["id"]),
            )
            db.commit()
            return PermissionDetail(
                id=existing["id"], user_id=req.user_id,
                resource_type=req.resource_type, resource_id=req.resource_id,
                access=req.access, role=req.role,
            )

        cursor = db.execute(
            "INSERT INTO permissions (user_id,resource_type,resource_id,access,role) VALUES (?,?,?,?,?)",
            (req.user_id, req.resource_type, req.resource_id, req.access, req.role),
        )
        db.commit()
        return PermissionDetail(
            id=cursor.lastrowid, user_id=req.user_id,
            resource_type=req.resource_type, resource_id=req.resource_id,
            access=req.access, role=req.role,
        )
    finally:
        db.close()


@router.get("/users/{user_id}/permissions", response_model=list[PermissionDetail])
def list_user_permissions(user_id: str, user: dict = Depends(require_admin)):
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM permissions WHERE user_id=?", (user_id,)).fetchall()
        return [
            PermissionDetail(id=r["id"], user_id=r["user_id"], resource_type=r["resource_type"],
                             resource_id=r["resource_id"], access=r["access"])
            for r in rows
        ]
    finally:
        db.close()


@router.delete("/permissions/{perm_id}", status_code=204)
def revoke_permission(perm_id: int, user: dict = Depends(require_admin)):
    db = get_db()
    try:
        r = db.execute("SELECT 1 FROM permissions WHERE id=?", (perm_id,)).fetchone()
        if not r:
            raise APIError("not_found", "permission not found", 404)
        db.execute("DELETE FROM permissions WHERE id=?", (perm_id,))
        db.commit()
    finally:
        db.close()
