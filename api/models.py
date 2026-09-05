from pydantic import BaseModel, Field
from typing import Optional


# --- Cluster ---

class CreateClusterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=63, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$')
    node_count: int = Field(..., ge=1, le=20)
    environment_id: Optional[str] = None   # required for non-admin users


class CreateClusterResponse(BaseModel):
    cluster_id: str
    job_id: str


class UpdateClusterRequest(BaseModel):
    environment_id: Optional[str] = None


class ClusterNodes(BaseModel):
    control_plane: list[str]
    workers: list[str]


class ClusterDetail(BaseModel):
    id: str
    name: str
    node_count: int
    control_plane_count: int
    worker_count: int
    status: str
    nodes: ClusterNodes
    environment_id: Optional[str] = None
    latest_job_id: Optional[str] = None
    created_at: str


# --- Environment ---

class CreateEnvironmentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=63, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$')


class EnvironmentDetail(BaseModel):
    id: str
    name: str
    status: str
    clusters: list[str] = []
    created_at: str


# --- Job ---

class JobDetail(BaseModel):
    id: str
    cluster_id: str
    type: str
    status: str
    error: Optional[str] = None
    created_at: str


# --- Namespace ---

class CreateNamespaceRequest(BaseModel):
    project: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-z][a-z0-9-]*$')
    stage: str = Field(..., pattern=r'^(dev|staging|prod)$')


class NamespaceDetail(BaseModel):
    id: str
    cluster_id: str
    name: str
    project: Optional[str] = None
    stage: Optional[str] = None
    status: str
    created_at: str


# --- Auth ---

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    user: 'UserDetail'


# --- User ---

class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=63, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')
    name: str = Field(..., min_length=1, max_length=100)
    role: str = Field('viewer', pattern=r'^(admin|team_lead|developer|viewer)$')
    password: str = Field(..., min_length=4, max_length=128)


class UserDetail(BaseModel):
    id: str
    username: str
    name: str
    role: str
    api_key: Optional[str] = None
    is_active: bool
    created_at: str
    github_username: Optional[str] = None


class UpdateUserRoleRequest(BaseModel):
    role: str = Field(..., pattern=r'^(admin|team_lead|developer|viewer)$')


class PromoteDeploymentRequest(BaseModel):
    target_cluster_id: str
    target_namespace: Optional[str] = None   # defaults to same namespace as source


class GrantPermissionRequest(BaseModel):
    user_id: str
    resource_type: str = Field(..., pattern=r'^(cluster|namespace|environment)$')
    resource_id: str
    access: str = Field('read', pattern=r'^(read|write)$')
    # Effective role within this environment (only meaningful for resource_type=environment).
    # Cannot exceed the target user's global role ceiling.
    role: Optional[str] = Field(None, pattern=r'^(team_lead|developer|viewer)$')


class PermissionDetail(BaseModel):
    id: int
    user_id: str
    resource_type: str
    resource_id: str
    access: str
    role: Optional[str] = None


class EnvironmentMemberDetail(BaseModel):
    user_id: str
    username: str
    name: str
    global_role: str
    environment_role: Optional[str] = None   # their role specifically in this environment
    access: str


# ── Application (operator CR) ───────────────────────────────────────────

class ResourceQuotaRequest(BaseModel):
    cpu: Optional[str] = None
    memory: Optional[str] = None
    pods: Optional[str] = None


class LimitRangeRequest(BaseModel):
    default_cpu: Optional[str] = None
    default_memory: Optional[str] = None


class RBACRequest(BaseModel):
    owners: Optional[list[str]] = None
    viewers: Optional[list[str]] = None


class NetworkRequest(BaseModel):
    deny_all: Optional[bool] = False
    allow_from_namespaces: Optional[list[str]] = None


class MetricsRequest(BaseModel):
    enabled: bool = True
    port: Optional[str] = None
    path: Optional[str] = None


class CreateApplicationRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=63, pattern=r'^[a-z][a-z0-9-]*$')
    repo_url: str
    path: str
    revision: str = Field("main")
    namespace: str = Field(..., min_length=1, max_length=63)
    sync_policy: str = Field("auto", pattern=r'^(auto|manual)$')
    prune: bool = True
    self_heal: bool = True
    metrics: Optional[MetricsRequest] = None
    resource_quota: Optional[ResourceQuotaRequest] = None
    limit_range: Optional[LimitRangeRequest] = None
    rbac: Optional[RBACRequest] = None
    network: Optional[NetworkRequest] = None


class ApplicationDetail(BaseModel):
    name: str
    namespace: str
    destination_namespace: str
    repo_url: str
    path: str
    revision: str
    sync_policy: str
    created_at: str
