Provides a lightweight, intent-driven web interface to automate infrastructure-as-code on Proxmox VE. Instead of manual CLI execution or static CI/CD pushes, developers can describe their cluster needs (e.g., number of workers) via a web UI, which then orchestrates the Packer → Terraform pipeline.

# Local Setup & Testing
## 1. Prerequisites
-**Python 3.10+**

-**Terraform installed locally.**

-**SSH Tunnel (if working outside the university network):**

```bash
ssh -L 8006:10.40.19.230:8006 <e-number>@tesla.ce.pdn.ac.lk
```
## 2.Configure Credentials
Create a terraform/terraform.tfvars file (this is gitignored):

```powershell
Terraform
proxmox_api_url          = "https://localhost:8006/api2/json"
proxmox_api_token_id     = "YOUR_TOKEN_ID"
proxmox_api_token_secret = "YOUR_TOKEN_SECRET"
```
## 3. Run the Web App

```powershell
cd web-app
python3 -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
pip install flask
python app.py
```
Access the UI at http://localhost:5000


# Architecture Detail

- **Flask (Web-App)**  
  Captures user intent and triggers shell commands.

- **Terraform**  
  Manages the VM lifecycle and resource allocation.

- **Packer**  
  Creates the base Ubuntu Server 22.04 templates.

## Configuration Change Behavior

Not all configuration changes behave the same way in Terraform. Some changes require existing VMs to be destroyed and recreated, while others can be updated in place.

### 1. Settings that are "Initial Only" (Destroy & Recreate)

If you change these, Terraform usually cannot update the existing VM. Instead, it will delete the old VM and create a new one from scratch.

- **VM Name Prefix**  
  Changing this changes the unique ID Terraform uses to track the VM. Terraform will treat the old VMs as different resources, delete them, and create new ones.

- **IP Start / IP Prefix**  
  Changing the IP address usually requires a replacement because the Cloud-init configuration must be rewritten and the VM rebooted.

- **Template ID**  
  If you change the source image, Terraform must delete the current VM and clone a new one from the updated template.

- **Target Node**  
  You cannot move a VM to a different physical Proxmox node using this workflow without deleting and recreating it on the new node.

### 2. Settings that can be Updated (In-Place)

If you change these, Terraform will usually update the existing VMs without deleting them, although a reboot may still be required.

- **Control Plane / Worker Cores & RAM**  
  Terraform tells Proxmox to resize the VM hardware. The VM will usually reboot for the new CPU and memory settings to take effect.

- **Bridge**  
  Terraform can update the VM’s virtual network interface to use a different bridge.

- **DNS Server**  
  This updates the Cloud-init network configuration.

### 3. Settings that Add / Remove VMs (Scaling)

Changes to node counts are handled as scaling operations.

- **Control Planes / Worker Nodes**  
  - If you increase the count (for example, from 2 to 3), Terraform will create the additional VM.
  - If you decrease the count (for example, from 3 to 2), Terraform will delete the extra VM.

