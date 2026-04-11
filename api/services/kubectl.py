import logging
import paramiko

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


def run_kubectl(cluster_id: str, args: list[str], timeout: int = 30, stdin_data: str | None = None) -> str:
    """Run a kubectl command on the cluster's control plane via Paramiko SSH through a Jump Host.

    Args:
        cluster_id:  The cluster to target.
        args:        kubectl argument list (e.g. ["apply", "-f", "-"]).
        timeout:     Per-operation socket timeout in seconds.
        stdin_data:  Optional text to write to the command's stdin (e.g. a YAML manifest).
    """
    ip = _get_control_plane_ip(cluster_id)
    base = config.read_base_tfvars()

    # Target VM credentials
    target_user = base.get("ssh_user", "ubuntu")
    target_password = base.get("ssh_password", config.CLUSTER_SSH_PASSWORD)

    cmd = "kubectl " + " ".join(args)
    logger.info("[%s] %s on %s", cluster_id, cmd, ip)

    # Parse Jump Host config 
    jump_user, jump_host = config.JUMP_HOST.split("@")

    jump_client = paramiko.SSHClient()
    jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    target_client = paramiko.SSHClient()
    target_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        #Connect to the Jump Host first -change the cofig.
        jump_client.connect(
            jump_host, 
            username=jump_user, 
            look_for_keys=True, 
            timeout=10
        )

        #Open a direct TCP tunnel from the Jump Host to the Target VM
        jump_transport = jump_client.get_transport()
        dest_addr = (ip, 22)
        local_addr = ('127.0.0.1', 0) 
        tunnel_channel = jump_transport.open_channel("direct-tcpip", dest_addr, local_addr)

        # Connect to the Target VM through the tunnel using the password
        target_client.connect(
            ip, 
            username=target_user, 
            password=target_password, 
            sock=tunnel_channel, 
            look_for_keys=False, 
            timeout=10
        )

        # Execute the command on the target VM
        stdin, stdout, stderr = target_client.exec_command(cmd, timeout=timeout)
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
        target_client.close()
        jump_client.close()