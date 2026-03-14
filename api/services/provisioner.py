import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone

from .. import config
from ..database import get_db

logger = logging.getLogger(__name__)


def provision_cluster_async(cluster_id: str, job_id: str):
    thread = threading.Thread(target=_provision_cluster, args=(cluster_id, job_id), daemon=True)
    thread.start()


def destroy_cluster_async(cluster_id: str, job_id: str):
    thread = threading.Thread(target=_destroy_cluster, args=(cluster_id, job_id), daemon=True)
    thread.start()


# -- internal helpers --

def _update_job(job_id: str, status: str, error: str | None = None):
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?", (status, error, now, job_id))
    db.commit()
    db.close()


def _update_cluster(cluster_id: str, status: str):
    db = get_db()
    db.execute("UPDATE clusters SET status=? WHERE id=?", (status, cluster_id))
    db.commit()
    db.close()


def _setup_workspace(cluster_id: str) -> str:
    """Create workspace with symlinked .tf files. Returns terraform workspace path."""
    workspace = os.path.join(config.WORKSPACES_DIR, cluster_id)
    tf_dir = os.path.join(workspace, 'terraform')
    # ansible dir will be created by terraform's local_file resource
    os.makedirs(tf_dir, exist_ok=True)
    os.makedirs(os.path.join(workspace, 'ansible'), exist_ok=True)

    for f in os.listdir(config.TERRAFORM_DIR):
        if f.endswith('.tf'):
            src = os.path.join(config.TERRAFORM_DIR, f)
            dst = os.path.join(tf_dir, f)
            if not os.path.exists(dst):
                shutil.copy(src, dst)
    return tf_dir


def _generate_tfvars(tf_dir: str, cluster_id: str):
    db = get_db()
    cluster = db.execute("SELECT * FROM clusters WHERE id=?", (cluster_id,)).fetchone()
    db.close()

    base = config.read_base_tfvars()
    #  some names the proxmox doesn't approve. For that as a fix - (example: "Test Cluster 1" -> "test-cluster-1")
    safe_name = re.sub(r'[^a-z0-9-]', '-', cluster["name"].lower())

    # NOTE: 
    lines = [
        f'proxmox_api_url          = "https://127.0.0.1:8006/api2/json"',
        f'proxmox_api_token_id     = "{base.get("proxmox_api_token_id", "")}"',
        f'proxmox_api_token_secret = "{base.get("proxmox_api_token_secret", "")}"',
        '',
        f'control_plane_count = {cluster["control_plane_count"]}',
        f'worker_count        = {cluster["worker_count"]}',
        f'vm_name_prefix      = "{cluster["name"]}"',
        f'vm_cores            = {base.get("vm_cores", 2)}',
        f'vm_memory           = {base.get("vm_memory", 2048)}',
        f'vm_ip_start         = {cluster["ip_start"]}',
        f'vm_ip_prefix        = "{config.VM_IP_PREFIX}"',
        f'vm_ip_gateway       = "{config.VM_IP_GATEWAY}"',
        f'vm_dns_server       = "{config.VM_DNS_SERVER}"',
    ]

    with open(os.path.join(tf_dir, 'terraform.tfvars'), 'w') as f:
        f.write('\n'.join(lines) + '\n')


def _run_cmd(args: list[str], cwd: str, env: dict | None = None, timeout: int = 1800) -> str:
    print(f"\n --- STARTING COMMAND: {' '.join(args)} ---\n")
    
    # Use Popen to stream logs in real-time
    process = subprocess.Popen(
        args, 
        cwd=cwd, 
        env=env, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        text=True,
        bufsize=1
    )
    
    output_lines = []
    for line in process.stdout:
        print(line, end="")  # Instantly prints to terminal
        output_lines.append(line)
        
    process.wait(timeout=timeout)
    full_output = "".join(output_lines)
    
    print(f"\n --- FINISHED COMMAND (Exit Code: {process.returncode}) ---\n")
    
    if process.returncode != 0:
        short_output = full_output[-2000:] if len(full_output) > 2000 else full_output
        raise RuntimeError(f"{args[0]} failed (exit {process.returncode}): {short_output}")
        
    return full_output


def _provision_cluster(cluster_id: str, job_id: str):
    try:
        _update_job(job_id, 'running')
        _update_cluster(cluster_id, 'creating')

        tf_dir = _setup_workspace(cluster_id)
        _generate_tfvars(tf_dir, cluster_id)

        logger.info("[%s] terraform init", cluster_id)
        _run_cmd(['terraform', 'init', '-input=false'], cwd=tf_dir)

        logger.info("[%s] terraform apply", cluster_id)
        _run_cmd(['terraform', 'apply', '-auto-approve', '-input=false'], cwd=tf_dir)

        # Run ansible with workspace inventory
        workspace = os.path.join(config.WORKSPACES_DIR, cluster_id)
        inventory = os.path.join(workspace, 'ansible', 'inventory.ini')
        site_yml = os.path.join(config.ANSIBLE_DIR, 'site.yml')

        env = os.environ.copy()
        env['ANSIBLE_CONFIG'] = os.path.join(config.ANSIBLE_DIR, 'ansible.cfg')
        
        # Hardened SSH connection for firewall drops
        env['ANSIBLE_HOST_KEY_CHECKING'] = 'False'
        env['ANSIBLE_SSH_ARGS'] = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ControlMaster=no -o ServerAliveInterval=30 -o ServerAliveCountMax=5 -o ConnectTimeout=60'
        env['ANSIBLE_RETRIES'] = '3'

        logger.info("[%s] ansible-playbook", cluster_id)
        _run_cmd(['ansible-playbook', '-i', inventory, site_yml], cwd=config.ANSIBLE_DIR, env=env, timeout=3600)

        _update_job(job_id, 'completed')
        _update_cluster(cluster_id, 'running')
        logger.info("[%s] provisioning complete", cluster_id)

    except Exception as e:
        print(f"\n[!!!] CRITICAL PROVISIONING ERROR: {e}\n")
        logger.error("[%s] provisioning failed: %s", cluster_id, e)
        _update_job(job_id, 'failed', str(e))
        _update_cluster(cluster_id, 'failed')


def _destroy_cluster(cluster_id: str, job_id: str):
    try:
        _update_job(job_id, 'running')
        _update_cluster(cluster_id, 'deleting')

        workspace = os.path.join(config.WORKSPACES_DIR, cluster_id)
        tf_dir = os.path.join(workspace, 'terraform')

        if os.path.exists(os.path.join(tf_dir, 'terraform.tfstate')):
            logger.info("[%s] terraform destroy", cluster_id)
            _run_cmd(['terraform', 'destroy', '-auto-approve', '-input=false'], cwd=tf_dir)

        _update_job(job_id, 'completed')
        _update_cluster(cluster_id, 'deleted')

        if os.path.exists(workspace):
            shutil.rmtree(workspace)

        db = get_db()
        db.execute("DELETE FROM ip_allocations WHERE cluster_id=?", (cluster_id,))
        db.commit()
        db.close()

        logger.info("[%s] destruction complete", cluster_id)

    except Exception as e:
        print(f"\n[!!!] CRITICAL DESTRUCTION ERROR: {e}\n")
        logger.error("[%s] destruction failed: %s", cluster_id, e)
        _update_job(job_id, 'failed', str(e))
        _update_cluster(cluster_id, 'failed')