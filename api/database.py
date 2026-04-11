import os
import sqlite3
import logging
from . import config

logger = logging.getLogger(__name__)

def get_db() -> sqlite3.Connection:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    # 1. Run the base schema
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS environments (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clusters (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            node_count INTEGER NOT NULL,
            control_plane_count INTEGER NOT NULL DEFAULT 1,
            worker_count INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'creating',
            ip_start INTEGER NOT NULL,
            environment_id TEXT REFERENCES environments(id) ON DELETE SET NULL,
            grafana_password TEXT, 
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            cluster_id TEXT NOT NULL REFERENCES clusters(id),
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ip_allocations (
            cluster_id TEXT PRIMARY KEY REFERENCES clusters(id),
            ip_start INTEGER NOT NULL,
            ip_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS namespaces (
            id TEXT PRIMARY KEY,
            cluster_id TEXT NOT NULL REFERENCES clusters(id),
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            UNIQUE(cluster_id, name)
        );

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            api_key TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            access TEXT NOT NULL DEFAULT 'read',
            UNIQUE(user_id, resource_type, resource_id)
        );
    """)
    
    # 2. Run migrations for existing databases
    _migrate(conn)
    
    conn.commit()
    conn.close()

def _migrate(conn: sqlite3.Connection):
    """Run necessary schema migrations for existing databases."""
    
    # Check if grafana_password column exists
    cursor = conn.execute("PRAGMA table_info(clusters)")
    columns = [row['name'] for row in cursor.fetchall()]

    if 'grafana_password' not in columns:
        logger.info("Migration: Adding grafana_password column to clusters table")
        conn.execute("ALTER TABLE clusters ADD COLUMN grafana_password TEXT")

    # Add project and stage columns to namespaces
    cursor = conn.execute("PRAGMA table_info(namespaces)")
    ns_columns = [row['name'] for row in cursor.fetchall()]

    if 'project' not in ns_columns:
        logger.info("Migration: Adding project column to namespaces table")
        conn.execute("ALTER TABLE namespaces ADD COLUMN project TEXT")
    if 'stage' not in ns_columns:
        logger.info("Migration: Adding stage column to namespaces table")
        conn.execute("ALTER TABLE namespaces ADD COLUMN stage TEXT")

    # Add role column to permissions (stores per-environment effective role)
    cursor = conn.execute("PRAGMA table_info(permissions)")
    perm_columns = [row['name'] for row in cursor.fetchall()]

    if 'role' not in perm_columns:
        logger.info("Migration: Adding role column to permissions table")
        conn.execute("ALTER TABLE permissions ADD COLUMN role TEXT")

    # Drop UNIQUE constraint on clusters.name if it still exists
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='clusters'").fetchone()
    if row and 'name TEXT UNIQUE' in row[0]:
        logger.info("Migration: Removing UNIQUE constraint from clusters.name")
        conn.executescript("""
            PRAGMA foreign_keys = OFF;
            ALTER TABLE clusters RENAME TO _clusters_old;
            CREATE TABLE clusters (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                node_count INTEGER NOT NULL,
                control_plane_count INTEGER NOT NULL DEFAULT 1,
                worker_count INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'creating',
                ip_start INTEGER NOT NULL,
                environment_id TEXT REFERENCES environments(id) ON DELETE SET NULL,
                grafana_password TEXT,
                created_at TEXT NOT NULL
            );
            INSERT INTO clusters (id, name, node_count, control_plane_count, worker_count, status, ip_start, environment_id, created_at)
            SELECT id, name, node_count, control_plane_count, worker_count, status, ip_start, environment_id, created_at 
            FROM _clusters_old;
            DROP TABLE _clusters_old;
            PRAGMA foreign_keys = ON;
        """)