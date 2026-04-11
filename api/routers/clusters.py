import secrets
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from .. import config
from ..auth import require_admin, require_team_lead, require_viewer, check_resource_access
from ..database import get_db
from ..errors import APIError
from ..models import (
    ClusterDetail,
    ClusterNodes,
    CreateClusterRequest,
    CreateClusterResponse,
    JobDetail,
    UpdateClusterRequest,
)
from ..services.provisioner import destroy_cluster_async, provision_cluster_async

router = APIRouter()


def _gen_id(prefix: str) -> str:
    chars = string.ascii_lowercase + string.digits
    return f"{prefix}_{''.join(secrets.choice(chars) for _ in range(8))}"


def _cluster_nodes(row) -> ClusterNodes:
    cp = row['control_plane_count']
    wk = row['worker_count']
    ip_start = row['ip_start']
    return ClusterNodes(
        control_plane=[f"{config.VM_IP_PREFIX}{ip_start + i}" for i in range(cp)],
        workers=[f"{config.VM_IP_PREFIX}{ip_start + cp + i}" for i in range(wk)],
    )


def _row_to_detail(r, latest_job_id=None) -> ClusterDetail:
    return ClusterDetail(
        id=r['id'],
        name=r['name'],
        node_count=r['node_count'],
        control_plane_count=r['control_plane_count'],
        worker_count=r['worker_count'],
        status=r['status'],
        nodes=_cluster_nodes(r),
        environment_id=r['environment_id'],
        latest_job_id=latest_job_id,
        created_at=r['created_at'],
    )


# --- Cluster endpoints ---

@router.post("/clusters", status_code=201, response_model=CreateClusterResponse)
def create_cluster(req: CreateClusterRequest, user: dict = Depends(require_team_lead)):
    # Admins can create clusters anywhere (environment_id optional).
    # team_leads must supply an environment_id they have write + team_lead role on.
    if user["role"] != "admin":
        if not req.environment_id:
            raise APIError("bad_request", "environment_id is required for non-admin users", 400)
        check_resource_access(
            user, "environment", req.environment_id,
            need_write=True, need_role="team_lead",
        )

    db = get_db()
    try:
        if db.execute("SELECT 1 FROM clusters WHERE name=? AND status!='deleted'", (req.name,)).fetchone():
            raise APIError("conflict", "cluster name already exists", 409)

        if req.environment_id:
            if not db.execute(
                "SELECT 1 FROM environments WHERE id=? AND status!='deleted'", (req.environment_id,)
            ).fetchone():
                raise APIError("not_found", "environment not found", 404)

        cluster_id = _gen_id("clu")
        job_id = _gen_id("job")
        worker_count = max(req.node_count - 1, 0)
        now = datetime.now(timezone.utc).isoformat()

        last = db.execute("SELECT MAX(ip_start + ip_count) AS next_ip FROM ip_allocations").fetchone()
        ip_start = last["next_ip"] if last and last["next_ip"] else config.VM_IP_START

        db.execute(
            "INSERT INTO clusters (id,name,node_count,control_plane_count,worker_count,status,ip_start,environment_id,created_at)"
            " VALUES (?,?,?,1,?,'creating',?,?,?)",
            (cluster_id, req.name, req.node_count, worker_count, ip_start, req.environment_id, now),
        )
        db.execute(
            "INSERT INTO jobs (id,cluster_id,type,status,created_at) VALUES (?,?,'create','pending',?)",
            (job_id, cluster_id, now),
        )
        db.execute(
            "INSERT INTO ip_allocations (cluster_id,ip_start,ip_count) VALUES (?,?,?)",
            (cluster_id, ip_start, req.node_count),
        )
        db.commit()
    finally:
        db.close()

    provision_cluster_async(cluster_id, job_id)
    return CreateClusterResponse(cluster_id=cluster_id, job_id=job_id)


@router.get("/clusters", response_model=list[ClusterDetail])
def list_clusters(user: dict = Depends(require_viewer)):
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM clusters WHERE status!='deleted' ORDER BY created_at DESC").fetchall()
        result = []
        for r in rows:
            job = db.execute(
                "SELECT id FROM jobs WHERE cluster_id=? ORDER BY created_at DESC LIMIT 1", (r['id'],)
            ).fetchone()
            result.append(_row_to_detail(r, latest_job_id=job['id'] if job else None))
        return result
    finally:
        db.close()


@router.get("/clusters/{cluster_id}", response_model=ClusterDetail)
def get_cluster(cluster_id: str, user: dict = Depends(require_viewer)):
    check_resource_access(user, "cluster", cluster_id)
    db = get_db()
    try:
        r = db.execute("SELECT * FROM clusters WHERE id=? AND status!='deleted'", (cluster_id,)).fetchone()
        if not r:
            raise APIError("not_found", "cluster not found", 404)
        return _row_to_detail(r)
    finally:
        db.close()


@router.patch("/clusters/{cluster_id}", response_model=ClusterDetail)
def update_cluster(cluster_id: str, req: UpdateClusterRequest, user: dict = Depends(require_admin)):
    db = get_db()
    try:
        r = db.execute("SELECT * FROM clusters WHERE id=? AND status!='deleted'", (cluster_id,)).fetchone()
        if not r:
            raise APIError("not_found", "cluster not found", 404)
        if req.environment_id is not None:
            env = db.execute("SELECT 1 FROM environments WHERE id=? AND status!='deleted'", (req.environment_id,)).fetchone()
            if not env:
                raise APIError("not_found", "environment not found", 404)
        db.execute("UPDATE clusters SET environment_id=? WHERE id=?", (req.environment_id, cluster_id))
        db.commit()
        r = db.execute("SELECT * FROM clusters WHERE id=?", (cluster_id,)).fetchone()
        return _row_to_detail(r)
    finally:
        db.close()


@router.delete("/clusters/{cluster_id}", status_code=202)
def delete_cluster(cluster_id: str, user: dict = Depends(require_admin)):
    db = get_db()
    try:
        r = db.execute(
            "SELECT 1 FROM clusters WHERE id=? AND status NOT IN ('deleted','deleting')", (cluster_id,)
        ).fetchone()
        if not r:
            raise APIError("not_found", "cluster not found", 404)

        job_id = _gen_id("job")
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO jobs (id,cluster_id,type,status,created_at) VALUES (?,?,'delete','pending',?)",
            (job_id, cluster_id, now),
        )
        db.commit()
    finally:
        db.close()

    destroy_cluster_async(cluster_id, job_id)
    return {"cluster_id": cluster_id, "job_id": job_id}


# --- Job endpoint ---

@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, user: dict = Depends(require_viewer)):
    db = get_db()
    try:
        r = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not r:
            raise APIError("not_found", "job not found", 404)
        return JobDetail(
            id=r['id'],
            cluster_id=r['cluster_id'],
            type=r['type'],
            status=r['status'],
            error=r['error'],
            created_at=r['created_at'],
        )
    finally:
        db.close()
