import os
import subprocess
from flask import Flask, render_template, request, Response

app = Flask(__name__)

# --- Directory Mapping ---
# Jumps up from /web-app/ to Root, then into /terraform
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERRAFORM_DIR = os.path.join(BASE_DIR, "terraform")

def run_command(command, cwd):
    """Executes a shell command and yields output line by line."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=True
    )
    for line in process.stdout:
        yield line
    process.wait()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/provision', methods=['POST'])
def provision():
    # Get count from user intent
    worker_count = request.form.get('worker_count', 2)

    def generate():
        yield f"🚀 Intent Received: Provisioning {worker_count} workers...\n"
        yield "--------------------------------------------------\n"
        
        # FIX: We use escaped double quotes \" for Windows compatibility
        # This prevents the 'Value for undeclared variable' error.
        tf_cmd = f"terraform init && terraform apply -auto-approve -var=\"worker_count={worker_count}\""
        
        for line in run_command(tf_cmd, TERRAFORM_DIR):
            yield line

        yield f"\n✅ Terraform process finished for {worker_count} workers."

    return Response(generate(), mimetype='text/plain')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)