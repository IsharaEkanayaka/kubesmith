import configparser
import logging
import os
import paramiko
import subprocess

from .. import config
from ..database import get_db

logger = logging.getLogger(__name__)


def _get_control_plane_ip(cluster_id: str) -> str:
    """Get the first control plane IP for a cluster."""
    db = get_db()
    try:
        row = db.execute("SELECT ip_start FROM clusters WHERE id=?", (cluster_id,)).fetchone()
        if not row:
            raise RuntimeError(f"cluster {cluster_id} not found")
        return f"{config.VM_IP_PREFIX}{row['ip_start']}"
    finally:
        db.close()


def _get_cluster_ssh_creds(cluster_id: str) -> tuple[str, str]:
    """Read SSH user and password from the cluster's workspace inventory.ini."""
    inventory_path = os.path.join(config.WORKSPACES_DIR, cluster_id, 'ansible', 'inventory.ini')
    if os.path.exists(inventory_path):
        parser = configparser.ConfigParser(allow_no_value=True)
        parser.read(inventory_path)
        if parser.has_section('k8s:vars'):
            user = parser.get('k8s:vars', 'ansible_user', fallback='ubuntu')
            password = parser.get('k8s:vars', 'ansible_password', fallback=config.CLUSTER_SSH_PASSWORD)
            return user, password
    return 'ubuntu', config.CLUSTER_SSH_PASSWORD


def run_kubectl(cluster_id: str, args: list[str], timeout: int = 30, stdin_data: str | None = None) -> str:
    """Run a kubectl command on the cluster's control plane via Paramiko SSH through a Jump Host.

    Args:
        cluster_id:  The cluster to target.
        args:        kubectl argument list (e.g. ["apply", "-f", "-"]).
        timeout:     Per-operation socket timeout in seconds.
        stdin_data:  Optional text to write to the command's stdin (e.g. a YAML manifest).
    """
    ip = _get_control_plane_ip(cluster_id)

    # Target VM credentials from workspace inventory
    target_user, target_password = _get_cluster_ssh_creds(cluster_id)

    cmd = "kubectl " + " ".join(args)
    logger.info("[%s] %s on %s", cluster_id, cmd, ip)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            ip,
            username=target_user,
            password=target_password,
            look_for_keys=False,
            allow_agent=False,
            timeout=10,
        )

        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        if stdin_data is not None:
            stdin.write(stdin_data.encode())
            stdin.channel.shutdown_write()
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()

        if exit_code != 0:
            raise RuntimeError(f"kubectl failed: {err}")

        return out

    finally:
        client.close()


def fetch_and_store_kubeconfig(cluster_id: str) -> str:
    """Fetch /etc/kubernetes/admin.conf and store it under the cluster workspace.

    Returns the local kubeconfig path.
    """
    ip = _get_control_plane_ip(cluster_id)

    # Target VM credentials from workspace inventory
    target_user, target_password = _get_cluster_ssh_creds(cluster_id)

    workspace_dir = os.path.join(config.WORKSPACES_DIR, cluster_id)
    os.makedirs(workspace_dir, exist_ok=True)
    local_path = os.path.join(workspace_dir, "kubeconfig")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            ip,
            username=target_user,
            password=target_password,
            look_for_keys=False,
            allow_agent=False,
            timeout=10,
        )

        sftp = client.open_sftp()
        try:
            sftp.get("/etc/kubernetes/admin.conf", local_path)
        finally:
            sftp.close()

        return local_path

    finally:
        client.close()


def _get_kubeconfig_path(cluster_id: str) -> str:
    return os.path.join(config.WORKSPACES_DIR, cluster_id, "kubeconfig")


def apply_manifest_with_kubeconfig(cluster_id: str, manifest: str, timeout: int = 30) -> str:
    """Apply a manifest using the stored kubeconfig on the API host."""
    kubeconfig_path = _get_kubeconfig_path(cluster_id)
    if not os.path.exists(kubeconfig_path):
        fetch_and_store_kubeconfig(cluster_id)

    cmd = ["kubectl", "--kubeconfig", kubeconfig_path, "apply", "-f", "-"]
    logger.info("[%s] %s", cluster_id, " ".join(cmd))

    result = subprocess.run(
        cmd,
        input=manifest,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"kubectl apply failed: {err}")

    return result.stdout.strip()