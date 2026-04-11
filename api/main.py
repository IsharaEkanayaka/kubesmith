import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .errors import APIError, api_error_handler
from .routers import auth, clusters, deployments, environments, namespaces, users, monitor

STATIC_DIR = pathlib.Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _ensure_admin()
    yield


def _ensure_admin():
    """Create a default admin user if none exists."""
    from .database import get_db
    from .auth import generate_api_key, hash_api_key, hash_password
    from datetime import datetime, timezone

    db = get_db()
    try:
        admin = db.execute("SELECT 1 FROM users WHERE role='admin' AND is_active=1").fetchone()
        if admin:
            return
        raw_key = generate_api_key()
        key_hash = hash_api_key(raw_key)
        pw_hash = hash_password("admin")
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO users (id,username,name,role,api_key,password_hash,is_active,created_at) VALUES (?,?,?,?,?,?,1,?)",
            ("usr_admin", "admin", "Admin", "admin", key_hash, pw_hash, now),
        )
        db.commit()
        logging.getLogger(__name__).info("Default admin created — username: admin / password: admin")
        # Also write to file so it's easy to retrieve
        key_file = pathlib.Path(__file__).parent.parent / "data" / "admin_key.txt"
        key_file.write_text(raw_key)
    finally:
        db.close()


app = FastAPI(title="K8s Cluster API", version="1.0.0", lifespan=lifespan)
app.add_exception_handler(APIError, api_error_handler)
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(clusters.router, prefix="/api/v1", tags=["clusters"])
app.include_router(environments.router, prefix="/api/v1", tags=["environments"])
app.include_router(namespaces.router, prefix="/api/v1", tags=["namespaces"])
app.include_router(deployments.router, prefix="/api/v1", tags=["deployments"])
app.include_router(users.router, prefix="/api/v1", tags=["users"])
app.include_router(monitor.router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")
