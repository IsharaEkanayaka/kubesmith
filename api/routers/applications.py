"""
Applications router — creates the Application CR in-cluster using the stored
kubeconfig (preferred) or fallback SSH-based kubectl.
"""
import secrets
import string
from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, Depends

from .. import config
from ..auth import require_developer, check_resource_access
from ..database import get_db
from ..errors import APIError
from ..models import CreateApplicationRequest, ApplicationDetail
from ..services.kubectl import apply_manifest_with_kubeconfig

router = APIRouter()


def _gen_id(prefix: str) -> str:
    chars = string.ascii_lowercase + string.digits
    return f"{prefix}_{''.join(secrets.choice(chars) for _ in range(8))}"


def _require_running_cluster(db, cluster_id: str) -> None:
    row = db.execute(
        "SELECT status FROM clusters WHERE id=? AND status!='deleted'", (cluster_id,)
    ).fetchone()
    if not row:
        raise APIError("not_found", "cluster not found", 404)
    if row["status"] != "running":
        raise APIError("bad_request", f"cluster is not running (status: {row['status']})", 400)


@router.post("/clusters/{cluster_id}/applications", status_code=201, response_model=ApplicationDetail)
def create_application(cluster_id: str, req: CreateApplicationRequest, user: dict = Depends(require_developer)):
    check_resource_access(user, "cluster", cluster_id, need_write=True)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)

        deploy_spec = {
            "syncPolicy": req.sync_policy,
            "prune": req.prune,
            "selfHeal": req.self_heal,
        }
        monitoring_spec = None
        if req.metrics is not None:
            monitoring_spec = {
                "metrics": {
                    "enabled": req.metrics.enabled,
                }
            }
            if req.metrics.port:
                monitoring_spec["metrics"]["port"] = req.metrics.port
            if req.metrics.path:
                monitoring_spec["metrics"]["path"] = req.metrics.path

        destination_spec: dict = {
            "namespace": req.namespace,
        }
        if req.resource_quota:
            rq = {}
            if req.resource_quota.cpu:
                rq["cpu"] = req.resource_quota.cpu
            if req.resource_quota.memory:
                rq["memory"] = req.resource_quota.memory
            if req.resource_quota.pods:
                rq["pods"] = req.resource_quota.pods
            if rq:
                destination_spec["resourceQuota"] = rq

        if req.limit_range:
            lr = {}
            if req.limit_range.default_cpu:
                lr["defaultCpu"] = req.limit_range.default_cpu
            if req.limit_range.default_memory:
                lr["defaultMemory"] = req.limit_range.default_memory
            if lr:
                destination_spec["limitRange"] = lr

        spec = {
            "source": {
                "repoUrl": req.repo_url,
                "path": req.path,
                "revision": req.revision,
            },
            "destination": destination_spec,
            "deploy": deploy_spec,
        }
        if monitoring_spec:
            spec["monitoring"] = monitoring_spec

        if req.rbac:
            rbac_spec = {}
            if req.rbac.owners:
                rbac_spec["owners"] = req.rbac.owners
            if req.rbac.viewers:
                rbac_spec["viewers"] = req.rbac.viewers
            if rbac_spec:
                spec["rbac"] = rbac_spec

        if req.network:
            net_spec = {}
            if req.network.deny_all is not None:
                net_spec["denyAll"] = req.network.deny_all
            if req.network.allow_from_namespaces:
                net_spec["allowFromNamespaces"] = req.network.allow_from_namespaces
            if net_spec:
                spec["network"] = net_spec

        cr = {
            "apiVersion": "platform.kubesmith.io/v1alpha1",
            "kind": "Application",
            "metadata": {
                "name": req.name,
                "namespace": config.APPLICATION_NAMESPACE,
            },
            "spec": spec,
        }

        cr_yaml = yaml.dump(cr, default_flow_style=False)

        try:
            apply_manifest_with_kubeconfig(cluster_id, cr_yaml, timeout=30)
        except Exception as e:
            raise APIError("internal", f"failed to apply Application CR: {e}", 500)

        return ApplicationDetail(
            name=req.name,
            namespace=config.APPLICATION_NAMESPACE,
            destination_namespace=req.namespace,
            repo_url=req.repo_url,
            path=req.path,
            revision=req.revision,
            sync_policy=req.sync_policy,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        db.close()
