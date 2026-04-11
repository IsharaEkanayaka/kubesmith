import secrets
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..auth import require_admin, require_viewer, check_resource_access
from ..database import get_db
from ..errors import APIError
from ..models import CreateEnvironmentRequest, EnvironmentDetail, EnvironmentMemberDetail

router = APIRouter()


def _gen_id(prefix: str) -> str:
    chars = string.ascii_lowercase + string.digits
    return f"{prefix}_{''.join(secrets.choice(chars) for _ in range(8))}"


def _row_to_detail(db, r) -> EnvironmentDetail:
    clusters = db.execute(
        "SELECT id FROM clusters WHERE environment_id=? AND status!='deleted'", (r['id'],)
    ).fetchall()
    return EnvironmentDetail(
        id=r['id'],
        name=r['name'],
        status=r['status'],
        clusters=[c['id'] for c in clusters],
        created_at=r['created_at'],
    )


@router.post("/environments", status_code=201, response_model=EnvironmentDetail)
def create_environment(req: CreateEnvironmentRequest, user: dict = Depends(require_admin)):
    db = get_db()
    try:
        if db.execute("SELECT 1 FROM environments WHERE name=? AND status!='deleted'", (req.name,)).fetchone():
            raise APIError("conflict", "environment name already exists", 409)

        env_id = _gen_id("env")
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO environments (id,name,status,created_at) VALUES (?,?,'active',?)",
            (env_id, req.name, now),
        )
        db.commit()
        return EnvironmentDetail(id=env_id, name=req.name, status='active', clusters=[], created_at=now)
    finally:
        db.close()


@router.get("/environments", response_model=list[EnvironmentDetail])
def list_environments(user: dict = Depends(require_viewer)):
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM environments WHERE status!='deleted' ORDER BY created_at DESC").fetchall()
        return [_row_to_detail(db, r) for r in rows]
    finally:
        db.close()


@router.get("/environments/{env_id}", response_model=EnvironmentDetail)
def get_environment(env_id: str, user: dict = Depends(require_viewer)):
    check_resource_access(user, "environment", env_id)
    db = get_db()
    try:
        r = db.execute("SELECT * FROM environments WHERE id=? AND status!='deleted'", (env_id,)).fetchone()
        if not r:
            raise APIError("not_found", "environment not found", 404)
        return _row_to_detail(db, r)
    finally:
        db.close()


@router.get("/environments/{env_id}/members", response_model=list[EnvironmentMemberDetail])
def list_members(env_id: str, user: dict = Depends(require_viewer)):
    check_resource_access(user, "environment", env_id)
    db = get_db()
    try:
        if not db.execute(
            "SELECT 1 FROM environments WHERE id=? AND status!='deleted'", (env_id,)
        ).fetchone():
            raise APIError("not_found", "environment not found", 404)

        rows = db.execute(
            "SELECT u.id, u.username, u.name, u.role AS global_role, "
            "       p.role AS environment_role, p.access "
            "FROM permissions p "
            "JOIN users u ON p.user_id = u.id "
            "WHERE p.resource_type='environment' AND p.resource_id=? AND u.is_active=1 "
            "ORDER BY u.name",
            (env_id,),
        ).fetchall()
        return [
            EnvironmentMemberDetail(
                user_id=r["id"],
                username=r["username"],
                name=r["name"],
                global_role=r["global_role"],
                environment_role=r["environment_role"],
                access=r["access"],
            )
            for r in rows
        ]
    finally:
        db.close()


@router.delete("/environments/{env_id}", status_code=204)
def delete_environment(env_id: str, user: dict = Depends(require_admin)):
    db = get_db()
    try:
        r = db.execute("SELECT 1 FROM environments WHERE id=? AND status!='deleted'", (env_id,)).fetchone()
        if not r:
            raise APIError("not_found", "environment not found", 404)

        has_clusters = db.execute(
            "SELECT 1 FROM clusters WHERE environment_id=? AND status NOT IN ('deleted')", (env_id,)
        ).fetchone()
        if has_clusters:
            raise APIError("conflict", "environment has active clusters", 409)

        db.execute("UPDATE environments SET status='deleted' WHERE id=?", (env_id,))
        db.commit()
    finally:
        db.close()
