import httpx
import base64
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from ..database import get_db
from ..services.kubectl import run_kubectl 

from ..config import (
    GRAFANA_URL,
    GRAFANA_USER,
    GRAFANA_DASHBOARD_PATH,
)

router = APIRouter(
    prefix="/monitor",
    tags=["Monitoring"]
)

def fetch_cluster_password(cluster_id: str) -> str:
    """Helper to grab the password from K8s via SSH/Kubectl."""
    args = [
        "get", "secret", "-n", "monitoring", 
        "monitoring-grafana", 
        "-o", "jsonpath='{.data.admin-password}'"
    ]
    encoded_pw = run_kubectl(cluster_id, args)
    clean_pw = encoded_pw.strip().strip("'").strip('"')
    return base64.b64decode(clean_pw).decode('utf-8')


# cluster_id from the URL
@router.get("/open/{cluster_id}")
async def open_monitor(cluster_id: str):
    db = get_db()
    try:
        cluster = db.execute(
            "SELECT id, grafana_password FROM clusters WHERE id = ?",
            (cluster_id,)
        ).fetchone()
        
        if not cluster:
            raise HTTPException(status_code=404, detail="Cluster not found in database.")
            
        password = cluster['grafana_password']
        
        # Fetching the password using SSH
        if not password:
            try:
                password = fetch_cluster_password(cluster_id)
                db.execute(
                    "UPDATE clusters SET grafana_password = ? WHERE id = ?", 
                    (password, cluster_id)
                )
                db.commit()
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to fetch password: {str(e)}")

        # Authentication to Grafana and redirect
        login_credentials = {
            "user": GRAFANA_USER,
            "password": password
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(f"{GRAFANA_URL}/login", json=login_credentials)

            if response.status_code == 200:
                session_cookie = response.cookies.get("grafana_session")
                target_url = f"{GRAFANA_URL}{GRAFANA_DASHBOARD_PATH}"
                redirect = RedirectResponse(url=target_url)

                redirect.set_cookie(
                    key="grafana_session",
                    value=session_cookie,
                    httponly=True
                )
                return redirect
            else:
                raise HTTPException(
                    status_code=500, 
                    detail="Internal monitoring authentication failed."
                )
    finally:
        db.close()