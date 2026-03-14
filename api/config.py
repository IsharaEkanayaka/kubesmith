import os
import re

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TERRAFORM_DIR = os.path.join(PROJECT_ROOT, 'terraform')
ANSIBLE_DIR = os.path.join(PROJECT_ROOT, 'ansible')
WORKSPACES_DIR = os.path.join(PROJECT_ROOT, 'workspaces')
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
DB_PATH = os.path.join(DATA_DIR, 'api.db')

GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000")
GRAFANA_USER = os.getenv("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.getenv("GRAFANA_PASSWORD", "SPskU5XpwtTtAnEdXwM34WbQ5E9Nsna377nb8ql8")
GRAFANA_DASHBOARD_PATH = os.getenv("GRAFANA_DASHBOARD_PATH", "/d/efa86fd1d0c121a26444b636a3f509a8/kubernetes-compute-resources-cluster?orgId=1&from=now-1h&to=now&timezone=utc&var-datasource=default&var-cluster=&refresh=10s")

# Network defaults (override via environment variables)
VM_IP_PREFIX = os.getenv('VM_IP_PREFIX', '10.40.19.')
VM_IP_START = int(os.getenv('VM_IP_START', '201'))
VM_IP_GATEWAY = os.getenv('VM_IP_GATEWAY', '10.40.19.254')
VM_DNS_SERVER = os.getenv('VM_DNS_SERVER', '10.40.2.1')

# --- NEW: SSH & Jump Host Configuration ---
CLUSTER_SSH_PASSWORD = os.getenv('CLUSTER_SSH_PASSWORD', 'your_ssh_password_here')
JUMP_HOST = os.getenv('JUMP_HOST', 'user@tesla.ce.pdn.ac.lk')

def read_base_tfvars() -> dict:
    """Read Proxmox credentials and defaults from the base terraform.tfvars."""
    tfvars: dict = {}
    tfvars_path = os.path.join(TERRAFORM_DIR, 'terraform.tfvars')
    if not os.path.exists(tfvars_path):
        return tfvars
    with open(tfvars_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # String values
            match = re.match(r'^(\w+)\s*=\s*"(.+)"', line)
            if match:
                tfvars[match.group(1)] = match.group(2)
                continue
            # Numeric values
            match = re.match(r'^(\w+)\s*=\s*(\d+)', line)
            if match:
                tfvars[match.group(1)] = int(match.group(2))
    return tfvars
