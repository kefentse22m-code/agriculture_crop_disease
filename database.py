import os
import time
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
)
SUPABASE_UPLOAD_BUCKET = os.environ.get("SUPABASE_UPLOAD_BUCKET", "").strip()


class DuplicateUserError(Exception):
    pass


class SupabaseConfigError(RuntimeError):
    pass


def supabase_enabled():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def require_supabase_config():
    if not supabase_enabled():
        raise SupabaseConfigError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env."
        )


def supabase_headers(prefer=None):
    require_supabase_config()
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def rest_url(table):
    require_supabase_config()
    return f"{SUPABASE_URL}/rest/v1/{table}"


def init_db():
    require_supabase_config()
    ensure_default_admin()


def ensure_default_admin():
    if find_user_by_email("admin@smartfarm.local"):
        return
    try:
        create_user(
            "Admin",
            "admin@smartfarm.local",
            generate_password_hash("admin123"),
            "admin",
        )
    except DuplicateUserError:
        pass


def create_user(name, email, password_hash, role="user"):
    payload = {
        "name": name,
        "email": email,
        "password_hash": password_hash,
        "role": role,
    }
    response = requests.post(
        rest_url("users"),
        headers=supabase_headers("return=representation"),
        json=payload,
        timeout=10,
    )
    if response.status_code == 409:
        raise DuplicateUserError()
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else payload


def find_user_by_email(email):
    response = requests.get(
        rest_url("users"),
        headers=supabase_headers(),
        params={"email": f"eq.{email}", "select": "*", "limit": "1"},
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


def find_user_by_id(user_id):
    response = requests.get(
        rest_url("users"),
        headers=supabase_headers(),
        params={"id": f"eq.{user_id}", "select": "*", "limit": "1"},
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


def get_all_users():
    response = requests.get(
        rest_url("users"),
        headers=supabase_headers(),
        params={"select": "id,name,email,role,created_at", "order": "created_at.desc"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def record_auth_event(user_id, email, event_type, success=True, details=None):
    row = {
        "user_id": user_id,
        "email": email,
        "event_type": event_type,
        "success": bool(success),
        "details": details or {},
    }

    response = requests.post(
        rest_url("auth_events"),
        headers=supabase_headers("return=minimal"),
        json=row,
        timeout=10,
    )
    response.raise_for_status()
    return row


def upload_file(project, user_id, filename, content_type, file_bytes):
    if not SUPABASE_UPLOAD_BUCKET or not file_bytes:
        return {"path": None, "public_url": None}

    safe_name = secure_filename(filename or "upload.jpg") or "upload.jpg"
    storage_path = f"{user_id}/{project}/{int(time.time() * 1000)}_{safe_name}"
    encoded_path = quote(storage_path, safe="/")
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_UPLOAD_BUCKET}/{encoded_path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "false",
    }
    response = requests.post(url, headers=headers, data=file_bytes, timeout=20)
    response.raise_for_status()

    public_url = (
        f"{SUPABASE_URL}/storage/v1/object/public/"
        f"{SUPABASE_UPLOAD_BUCKET}/{encoded_path}"
    )
    return {"path": storage_path, "public_url": public_url}


def record_project_history(
    user_id,
    project,
    action,
    request_payload=None,
    response_payload=None,
    upload_filename=None,
    upload_content_type=None,
    file_bytes=None,
):
    if not user_id:
        return None

    uploaded = upload_file(project, user_id, upload_filename, upload_content_type, file_bytes)
    row = {
        "user_id": user_id,
        "project": project,
        "action": action,
        "upload_filename": upload_filename,
        "upload_content_type": upload_content_type,
        "upload_storage_path": uploaded["path"],
        "upload_public_url": uploaded["public_url"],
        "request_payload": request_payload or {},
        "response_payload": response_payload or {},
    }

    response = requests.post(
        rest_url("project_history"),
        headers=supabase_headers("return=representation"),
        json=row,
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else row


def get_user_history(user_id, limit=50):
    response = requests.get(
        rest_url("project_history"),
        headers=supabase_headers(),
        params={
            "user_id": f"eq.{user_id}",
            "select": "*",
            "order": "created_at.desc",
            "limit": str(limit),
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
