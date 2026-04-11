import secrets
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..auth import require_team_lead, require_viewer, check_resource_access
from ..database import get_db
from ..errors import APIError
from ..models import CreateNamespaceRequest, NamespaceDetail
from ..services.kubectl import run_kubectl

router = APIRouter()

RESERVED_NAMESPACES = {"default", "kube-system", "kube-public", "kube-node-lease"}


def _gen_id(prefix: str) -> str:
    chars = string.ascii_lowercase + string.digits
    return f"{prefix}_{''.join(secrets.choice(chars) for _ in range(8))}"


def _require_running_cluster(db, cluster_id: str):
    row = db.execute(
        "SELECT status FROM clusters WHERE id=? AND status!='deleted'", (cluster_id,)
    ).fetchone()
    if not row:
        raise APIError("not_found", "cluster not found", 404)
    if row["status"] != "running":
        raise APIError("bad_request", f"cluster is not running (status: {row['status']})", 400)


@router.post("/clusters/{cluster_id}/namespaces", status_code=201, response_model=NamespaceDetail)
def create_namespace(cluster_id: str, req: CreateNamespaceRequest, user: dict = Depends(require_team_lead)):
    check_resource_access(user, "cluster", cluster_id, need_write=True)

    ns_name = f"{req.project}-{req.stage}"

    if ns_name in RESERVED_NAMESPACES:
        raise APIError("bad_request", f"'{ns_name}' is a reserved namespace", 400)

    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)

        existing = db.execute(
            "SELECT 1 FROM namespaces WHERE cluster_id=? AND name=? AND status='active'",
            (cluster_id, ns_name),
        ).fetchone()
        if existing:
            raise APIError("conflict", f"namespace '{ns_name}' already exists in this cluster", 409)

        try:
            run_kubectl(cluster_id, ["create", "namespace", ns_name])
        except Exception as e:
            raise APIError("internal", f"failed to create namespace on cluster: {e}", 500)

        ns_id = _gen_id("ns")
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO namespaces (id,cluster_id,name,project,stage,status,created_at) VALUES (?,?,?,?,?,'active',?)",
            (ns_id, cluster_id, ns_name, req.project, req.stage, now),
        )
        db.commit()
        return NamespaceDetail(
            id=ns_id, cluster_id=cluster_id, name=ns_name,
            project=req.project, stage=req.stage,
            status="active", created_at=now,
        )
    finally:
        db.close()


@router.get("/clusters/{cluster_id}/namespaces", response_model=list[NamespaceDetail])
def list_namespaces(cluster_id: str, user: dict = Depends(require_viewer)):
    check_resource_access(user, "cluster", cluster_id)
    db = get_db()
    try:
        cluster = db.execute("SELECT 1 FROM clusters WHERE id=? AND status!='deleted'", (cluster_id,)).fetchone()
        if not cluster:
            raise APIError("not_found", "cluster not found", 404)

        rows = db.execute(
            "SELECT * FROM namespaces WHERE cluster_id=? AND status='active' ORDER BY created_at",
            (cluster_id,),
        ).fetchall()
        return [
            NamespaceDetail(
                id=r["id"], cluster_id=r["cluster_id"], name=r["name"],
                project=r["project"], stage=r["stage"],
                status=r["status"], created_at=r["created_at"],
            )
            for r in rows
        ]
    finally:
        db.close()


@router.get("/clusters/{cluster_id}/namespaces/{ns_id}", response_model=NamespaceDetail)
def get_namespace(cluster_id: str, ns_id: str, user: dict = Depends(require_viewer)):
    check_resource_access(user, "cluster", cluster_id)
    db = get_db()
    try:
        r = db.execute(
            "SELECT * FROM namespaces WHERE id=? AND cluster_id=? AND status='active'",
            (ns_id, cluster_id),
        ).fetchone()
        if not r:
            raise APIError("not_found", "namespace not found", 404)
        return NamespaceDetail(
            id=r["id"], cluster_id=r["cluster_id"], name=r["name"],
            project=r["project"], stage=r["stage"],
            status=r["status"], created_at=r["created_at"],
        )
    finally:
        db.close()


@router.delete("/clusters/{cluster_id}/namespaces/{ns_id}", status_code=204)
def delete_namespace(cluster_id: str, ns_id: str, user: dict = Depends(require_team_lead)):
    check_resource_access(user, "cluster", cluster_id, need_write=True)
    db = get_db()
    try:
        r = db.execute(
            "SELECT * FROM namespaces WHERE id=? AND cluster_id=? AND status='active'",
            (ns_id, cluster_id),
        ).fetchone()
        if not r:
            raise APIError("not_found", "namespace not found", 404)

        _require_running_cluster(db, cluster_id)
        try:
            run_kubectl(cluster_id, ["delete", "namespace", r["name"]])
        except Exception as e:
            raise APIError("internal", f"failed to delete namespace on cluster: {e}", 500)

        db.execute("UPDATE namespaces SET status='deleted' WHERE id=?", (ns_id,))
        db.commit()
    finally:
        db.close()
