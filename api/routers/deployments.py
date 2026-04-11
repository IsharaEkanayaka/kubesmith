"""
Deployments router — bridges the kubesmith HTTP API to AppDeployment and
AppMonitor custom resources on provisioned clusters via kubectl.

The kubesmith-operator running inside each cluster does the actual heavy
lifting (Helm install, manifest apply, ServiceMonitor creation).  kubesmith
only needs to create / list / delete the CRs and read back their status.
"""
import json
import secrets
import string
from datetime import datetime, timezone
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, Query

from ..auth import require_team_lead, require_viewer, check_resource_access
from ..database import get_db
from ..errors import APIError
from ..models import (
    CreateAppDeploymentRequest,
    AppDeploymentDetail,
    PromoteDeploymentRequest,
    CreateAppMonitorRequest,
    AppMonitorDetail,
)
from ..services.kubectl import run_kubectl

router = APIRouter()

# ── helpers ───────────────────────────────────────────────────────────────────

def _require_running_cluster(db, cluster_id: str) -> None:
    row = db.execute(
        "SELECT status FROM clusters WHERE id=? AND status!='deleted'", (cluster_id,)
    ).fetchone()
    if not row:
        raise APIError("not_found", "cluster not found", 404)
    if row["status"] != "running":
        raise APIError("bad_request", f"cluster is not running (status: {row['status']})", 400)


def _cr_to_appdep(item: dict) -> AppDeploymentDetail:
    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    helm = spec.get("helm") or {}
    return AppDeploymentDetail(
        name=meta.get("name", ""),
        namespace=meta.get("namespace", ""),
        deploy_type=spec.get("type", ""),
        chart_name=helm.get("chart"),
        chart_version=helm.get("version"),
        phase=status.get("phase", "Unknown"),
        message=status.get("message"),
        ready_pods=status.get("readyPods", 0),
        total_pods=status.get("totalPods", 0),
        last_deployed_at=status.get("lastDeployedAt"),
        created_at=meta.get("creationTimestamp", ""),
    )


def _cr_to_appmon(item: dict) -> AppMonitorDetail:
    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    return AppMonitorDetail(
        name=meta.get("name", ""),
        namespace=meta.get("namespace", ""),
        app_deployment_ref=spec.get("appDeploymentRef", ""),
        health=status.get("health", "Unknown"),
        service_monitor_created=status.get("serviceMonitorCreated", False),
        prometheus_rule_created=status.get("prometheusRuleCreated", False),
        created_at=meta.get("creationTimestamp", ""),
    )


# ── AppDeployment endpoints ───────────────────────────────────────────────────

@router.get("/clusters/{cluster_id}/deployments", response_model=list[AppDeploymentDetail])
def list_deployments(cluster_id: str, user: dict = Depends(require_viewer)):
    check_resource_access(user, "cluster", cluster_id)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)
        try:
            out = run_kubectl(cluster_id, ["get", "appdeployments", "-A", "-o", "json"], timeout=15)
        except Exception as e:
            raise APIError("internal", f"kubectl get appdeployments: {e}", 500)
        data = json.loads(out)
        return [_cr_to_appdep(item) for item in data.get("items", [])]
    finally:
        db.close()


@router.get("/clusters/{cluster_id}/deployments/{name}", response_model=AppDeploymentDetail)
def get_deployment(cluster_id: str, name: str, namespace: str = Query("default"), user: dict = Depends(require_viewer)):
    check_resource_access(user, "cluster", cluster_id)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)
        try:
            out = run_kubectl(cluster_id,
                              ["get", "appdeployment", name, "-n", namespace, "-o", "json"],
                              timeout=15)
        except Exception as e:
            raise APIError("not_found", f"deployment {name!r} not found: {e}", 404)
        return _cr_to_appdep(json.loads(out))
    finally:
        db.close()


@router.post("/clusters/{cluster_id}/deployments", status_code=201, response_model=AppDeploymentDetail)
def create_deployment(cluster_id: str, req: CreateAppDeploymentRequest, user: dict = Depends(require_team_lead)):
    check_resource_access(user, "cluster", cluster_id, need_write=True)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)

        # Validate fields per type
        if req.deploy_type == "helm":
            if not req.chart_repo or not req.chart_name:
                raise APIError("bad_request", "helm deployments require chart_repo and chart_name", 400)
        elif req.deploy_type == "manifest":
            if not req.manifest:
                raise APIError("bad_request", "manifest deployments require manifest content", 400)
        else:
            raise APIError("bad_request", "deploy_type must be 'helm' or 'manifest'", 400)

        # Build the AppDeployment CR
        spec: dict = {"type": req.deploy_type}
        if req.deploy_type == "helm":
            helm_spec: dict = {"repoUrl": req.chart_repo, "chart": req.chart_name}
            if req.chart_version:
                helm_spec["version"] = req.chart_version
            if req.values_override:
                helm_spec["values"] = req.values_override
            spec["helm"] = helm_spec
        else:
            spec["manifest"] = req.manifest
            if req.pod_selector:
                spec["podSelector"] = req.pod_selector

        cr = {
            "apiVersion": "kubesmith.io/v1alpha1",
            "kind": "AppDeployment",
            "metadata": {"name": req.name, "namespace": req.namespace},
            "spec": spec,
        }
        cr_yaml = yaml.dump(cr, default_flow_style=False)

        try:
            run_kubectl(cluster_id, ["apply", "-f", "-"], stdin_data=cr_yaml, timeout=30)
        except Exception as e:
            raise APIError("internal", f"failed to apply AppDeployment CR: {e}", 500)

        return AppDeploymentDetail(
            name=req.name,
            namespace=req.namespace,
            deploy_type=req.deploy_type,
            chart_name=req.chart_name,
            chart_version=req.chart_version,
            phase="Installing",
            message="Deployment CR created; operator is reconciling",
            ready_pods=0,
            total_pods=0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        db.close()


@router.delete("/clusters/{cluster_id}/deployments/{name}", status_code=204)
def delete_deployment(cluster_id: str, name: str, namespace: str = Query("default"), user: dict = Depends(require_team_lead)):
    check_resource_access(user, "cluster", cluster_id, need_write=True)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)
        try:
            run_kubectl(cluster_id,
                        ["delete", "appdeployment", name, "-n", namespace, "--ignore-not-found"],
                        timeout=30)
        except Exception as e:
            raise APIError("internal", f"failed to delete AppDeployment CR: {e}", 500)
    finally:
        db.close()


@router.post("/clusters/{cluster_id}/deployments/{name}/promote", response_model=AppDeploymentDetail)
def promote_deployment(
    cluster_id: str,
    name: str,
    req: PromoteDeploymentRequest,
    namespace: str = Query("default"),
    user: dict = Depends(require_team_lead),
):
    """
    Copy an AppDeployment CR from the source cluster to a target cluster.
    Strips all server-side metadata so it applies cleanly as a new resource.
    """
    check_resource_access(user, "cluster", cluster_id)
    check_resource_access(user, "cluster", req.target_cluster_id, need_write=True)

    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)
        _require_running_cluster(db, req.target_cluster_id)

        # Fetch the CR from source cluster
        try:
            out = run_kubectl(
                cluster_id,
                ["get", "appdeployment", name, "-n", namespace, "-o", "json"],
                timeout=15,
            )
        except Exception as e:
            raise APIError("not_found", f"deployment {name!r} not found in source cluster: {e}", 404)

        cr = json.loads(out)

        # Strip server-side fields so the CR can be applied fresh on the target cluster
        meta = cr.get("metadata", {})
        for field in ("resourceVersion", "uid", "creationTimestamp", "generation",
                      "managedFields", "finalizers", "selfLink"):
            meta.pop(field, None)
        cr.pop("status", None)

        # Set target namespace (default: same as source)
        target_ns = req.target_namespace or namespace
        meta["namespace"] = target_ns
        cr["metadata"] = meta

        cr_yaml = yaml.dump(cr, default_flow_style=False)

        # Apply to target cluster
        try:
            run_kubectl(req.target_cluster_id, ["apply", "-f", "-"], stdin_data=cr_yaml, timeout=30)
        except Exception as e:
            raise APIError("internal", f"failed to promote AppDeployment to target cluster: {e}", 500)

        spec = cr.get("spec", {})
        helm = spec.get("helm") or {}
        return AppDeploymentDetail(
            name=name,
            namespace=target_ns,
            deploy_type=spec.get("type", ""),
            chart_name=helm.get("chart"),
            chart_version=helm.get("version"),
            phase="Installing",
            message=f"Promoted from cluster {cluster_id}/{namespace}; operator is reconciling",
            ready_pods=0,
            total_pods=0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        db.close()


# ── Pod status (live) ─────────────────────────────────────────────────────────

@router.get("/clusters/{cluster_id}/deployments/{name}/pods")
def list_pods(cluster_id: str, name: str, namespace: str = Query("default"), user: dict = Depends(require_viewer)):
    check_resource_access(user, "cluster", cluster_id)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)
        try:
            out = run_kubectl(cluster_id,
                              ["get", "pods", "-n", namespace,
                               "-l", f"app.kubernetes.io/instance={name}",
                               "-o", "json"],
                              timeout=15)
        except Exception as e:
            raise APIError("internal", f"kubectl get pods: {e}", 500)
        items = json.loads(out).get("items", [])
        pods = []
        for p in items:
            pmeta = p.get("metadata", {})
            pstatus = p.get("status", {})
            ready = any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in pstatus.get("conditions", [])
            )
            restarts = sum(
                cs.get("restartCount", 0)
                for cs in pstatus.get("containerStatuses", [])
            )
            pods.append({
                "name": pmeta.get("name"),
                "phase": pstatus.get("phase", "Unknown"),
                "ready": ready,
                "restarts": restarts,
                "node": pstatus.get("hostIP"),
                "started_at": pstatus.get("startTime"),
            })
        return {"pods": pods}
    finally:
        db.close()


@router.get("/clusters/{cluster_id}/deployments/{name}/pods/{pod_name}/logs")
def pod_logs(
    cluster_id: str,
    name: str,
    pod_name: str,
    namespace: str = Query("default"),
    tail: int = Query(200, ge=1, le=2000),
    user: dict = Depends(require_viewer),
):
    check_resource_access(user, "cluster", cluster_id)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)
        try:
            logs = run_kubectl(cluster_id,
                               ["logs", "-n", namespace, pod_name, f"--tail={tail}"],
                               timeout=30)
        except Exception as e:
            raise APIError("internal", f"kubectl logs: {e}", 500)
        return {"pod": pod_name, "logs": logs}
    finally:
        db.close()


# ── AppMonitor endpoints ──────────────────────────────────────────────────────

@router.get("/clusters/{cluster_id}/monitors", response_model=list[AppMonitorDetail])
def list_monitors(cluster_id: str, user: dict = Depends(require_viewer)):
    check_resource_access(user, "cluster", cluster_id)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)
        try:
            out = run_kubectl(cluster_id, ["get", "appmonitors", "-A", "-o", "json"], timeout=15)
        except Exception as e:
            raise APIError("internal", f"kubectl get appmonitors: {e}", 500)
        data = json.loads(out)
        return [_cr_to_appmon(item) for item in data.get("items", [])]
    finally:
        db.close()


@router.post("/clusters/{cluster_id}/monitors", status_code=201, response_model=AppMonitorDetail)
def create_monitor(cluster_id: str, req: CreateAppMonitorRequest, user: dict = Depends(require_team_lead)):
    check_resource_access(user, "cluster", cluster_id, need_write=True)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)

        spec: dict = {"appDeploymentRef": req.app_deployment_ref}
        if req.metrics_enabled is not None:
            spec["metrics"] = {
                "enabled": req.metrics_enabled,
                "port": req.metrics_port or "http",
                "path": req.metrics_path or "/metrics",
                "interval": req.metrics_interval or "30s",
            }
        if req.alerts:
            spec["alerts"] = req.alerts

        cr = {
            "apiVersion": "kubesmith.io/v1alpha1",
            "kind": "AppMonitor",
            "metadata": {"name": req.name, "namespace": req.namespace},
            "spec": spec,
        }
        cr_yaml = yaml.dump(cr, default_flow_style=False)

        try:
            run_kubectl(cluster_id, ["apply", "-f", "-"], stdin_data=cr_yaml, timeout=30)
        except Exception as e:
            raise APIError("internal", f"failed to apply AppMonitor CR: {e}", 500)

        return AppMonitorDetail(
            name=req.name,
            namespace=req.namespace,
            app_deployment_ref=req.app_deployment_ref,
            health="Unknown",
            service_monitor_created=False,
            prometheus_rule_created=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        db.close()


@router.delete("/clusters/{cluster_id}/monitors/{name}", status_code=204)
def delete_monitor(cluster_id: str, name: str, namespace: str = Query("default"), user: dict = Depends(require_team_lead)):
    check_resource_access(user, "cluster", cluster_id, need_write=True)
    db = get_db()
    try:
        _require_running_cluster(db, cluster_id)
        try:
            run_kubectl(cluster_id,
                        ["delete", "appmonitor", name, "-n", namespace, "--ignore-not-found"],
                        timeout=30)
        except Exception as e:
            raise APIError("internal", f"failed to delete AppMonitor CR: {e}", 500)
    finally:
        db.close()
