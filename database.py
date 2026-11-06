import os
import time
from datetime import datetime
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
    try:
        seed_demo_audit_logs()
    except Exception:
        pass
    try:
        seed_demo_irrigation_logs()
    except Exception:
        pass


def ensure_default_admin():
    admin_email = "admin@smartfarm.local"
    admin = find_user_by_email(admin_email)
    if admin:
        stored_hash = str(admin.get("password_hash") or "")
        if not stored_hash or not stored_hash.startswith("scrypt:"):
            try:
                response = requests.patch(
                    f"{SUPABASE_URL}/rest/v1/users?id=eq.{admin['id']}",
                    headers=supabase_headers("return=representation"),
                    json={
                        "password_hash": generate_password_hash("admin123"),
                        "role": "admin",
                        "email": admin_email,
                    },
                    timeout=10,
                )
                response.raise_for_status()
            except Exception as exc:
                print(f"Warning: Default admin password could not be repaired: {exc}")
        return

    try:
        create_user(
            "Admin",
            admin_email,
            generate_password_hash("admin123"),
            "admin",
        )
    except DuplicateUserError:
        pass


def create_user(name, email, password_hash, role="user", status="active", farm_name=None):
    normalized_role = str(role or "user").strip().lower()
    payload = {
        "name": name,
        "email": email,
        "password_hash": password_hash,
        "role": normalized_role,
        "status": str(status or "active").strip().lower(),
    }
    if farm_name is not None:
        payload["farm_name"] = farm_name
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


def get_all_irrigation_logs():
    response = requests.get(
        rest_url("irrigation"),
        headers=supabase_headers(),
        params={"select": "*", "order": "updated_at.desc"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def create_irrigation_log(
    farmer_name,
    farm_name,
    crop_name,
    variety,
    location,
    stage,
    soil_type,
    moisture_percent,
    temperature_c,
    humidity_percent,
    rain_3d_mm,
    last_watered,
    status,
    recommendation,
    user_id=None,
    farm_id=None,
):
    payload = {
        "farmer_name": farmer_name,
        "farm_name": farm_name,
        "crop_name": crop_name,
        "variety": variety,
        "location": location,
        "stage": stage,
        "soil_type": soil_type,
        "moisture_percent": moisture_percent,
        "temperature_c": temperature_c,
        "humidity_percent": humidity_percent,
        "rain_3d_mm": rain_3d_mm,
        "last_watered": last_watered,
        "status": str(status or "adequate").strip().lower(),
        "recommendation": recommendation,
        "updated_at": datetime.now().isoformat(),
    }
    if user_id is not None:
        payload["user_id"] = user_id
    if farm_id is not None:
        payload["farm_id"] = farm_id

    response = requests.post(
        rest_url("irrigation"),
        headers=supabase_headers("return=representation"),
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else payload


def seed_demo_irrigation_logs():
    try:
        existing = get_all_irrigation_logs()
        if existing:
            return existing
    except Exception:
        pass

    defaults = [
        {
            "farmer_name": "John Mokoa",
            "farm_name": "Mokoena Family Farm",
            "crop_name": "Maize",
            "variety": "PAN 5R-591",
            "location": "Hammanskraal, Zone 1",
            "stage": "Vegetative",
            "soil_type": "Loamy",
            "moisture_percent": 22,
            "temperature_c": 19,
            "humidity_percent": 95,
            "rain_3d_mm": 0,
            "last_watered": "2026-10-30",
            "status": "low soil moisture",
            "recommendation": "Irrigate now",
        },
        {
            "farmer_name": "John Mokoa",
            "farm_name": "Mokoena Family Farm",
            "crop_name": "Tomatoes",
            "variety": "Star 906",
            "location": "Hammanskraal, Zone 1",
            "stage": "Flowering",
            "soil_type": "Loamy",
            "moisture_percent": 38,
            "temperature_c": 19,
            "humidity_percent": 95,
            "rain_3d_mm": 6,
            "last_watered": "2026-10-29",
            "status": "adequate",
            "recommendation": "Monitor",
        },
        {
            "farmer_name": "Kagiso Baloyi",
            "farm_name": "Baloyi Holdings",
            "crop_name": "Maize",
            "variety": "PAN 5R-591",
            "location": "Hammanskraal, Zone 2",
            "stage": "Vegetative",
            "soil_type": "Loamy",
            "moisture_percent": 47,
            "temperature_c": 19,
            "humidity_percent": 95,
            "rain_3d_mm": 2,
            "last_watered": "2026-10-28",
            "status": "adequate",
            "recommendation": "Continue current cycle",
        },
        {
            "farmer_name": "Lerato Molefe",
            "farm_name": "HarvestCo Tunnels",
            "crop_name": "Tomatoes",
            "variety": "Rode",
            "location": "Bronkhorstspruit",
            "stage": "Fruiting",
            "soil_type": "Loamy",
            "moisture_percent": 41,
            "temperature_c": 18,
            "humidity_percent": 92,
            "rain_3d_mm": 15,
            "last_watered": "2026-10-27",
            "status": "adequate",
            "recommendation": "Maintain current schedule",
        },
        {
            "farmer_name": "Pieter van Wyk",
            "farm_name": "VW Farms",
            "crop_name": "Wheat",
            "variety": "SST 806",
            "location": "Hammanskraal, Zone 2",
            "stage": "Maturity",
            "soil_type": "Clay",
            "moisture_percent": 78,
            "temperature_c": 19,
            "humidity_percent": 95,
            "rain_3d_mm": 16,
            "last_watered": "2026-10-28",
            "status": "excess moisture",
            "recommendation": "Reduce irrigation",
        },
    ]

    created = []
    for item in defaults:
        created.append(create_irrigation_log(
            farmer_name=item["farmer_name"],
            farm_name=item["farm_name"],
            crop_name=item["crop_name"],
            variety=item["variety"],
            location=item["location"],
            stage=item["stage"],
            soil_type=item["soil_type"],
            moisture_percent=item["moisture_percent"],
            temperature_c=item["temperature_c"],
            humidity_percent=item["humidity_percent"],
            rain_3d_mm=item["rain_3d_mm"],
            last_watered=item["last_watered"],
            status=item["status"],
            recommendation=item["recommendation"],
        ))
    return created


def get_all_farms():
    response = requests.get(
        rest_url("farms"),
        headers=supabase_headers(),
        params={"select": "*", "order": "created_at.desc"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_all_crops():
    response = requests.get(
        rest_url("crops"),
        headers=supabase_headers(),
        params={"select": "*", "order": "created_at.desc"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def create_crop(crop_name, varieties, maturity_period, growth_stage, supported_disease_category, status="active"):
    payload = {
        "crop_name": crop_name,
        "varieties": varieties,
        "maturity_period": maturity_period,
        "growth_stage": growth_stage,
        "supported_disease_category": supported_disease_category,
        "status": str(status or "active").strip().lower(),
    }
    response = requests.post(
        rest_url("crops"),
        headers=supabase_headers("return=representation"),
        json=payload,
        timeout=10,
    )
    if response.status_code == 400 and "status" in payload and "Could not find the 'status' column of 'crops'" in response.text:
        payload.pop("status")
        response = requests.post(
            rest_url("crops"),
            headers=supabase_headers("return=representation"),
            json=payload,
            timeout=10,
        )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else payload


def update_crop(crop_id, crop_name=None, varieties=None, maturity_period=None, growth_stage=None, supported_disease_category=None, status=None):
    payload = {}
    if crop_name is not None:
        payload["crop_name"] = crop_name
    if varieties is not None:
        payload["varieties"] = varieties
    if maturity_period is not None:
        payload["maturity_period"] = maturity_period
    if growth_stage is not None:
        payload["growth_stage"] = growth_stage
    if supported_disease_category is not None:
        payload["supported_disease_category"] = supported_disease_category
    if status is not None:
        payload["status"] = str(status or "active").strip().lower()

    if not payload:
        return None

    response = requests.patch(
        rest_url("crops"),
        headers=supabase_headers("return=representation"),
        params={"id": f"eq.{crop_id}"},
        json=payload,
        timeout=10,
    )
    if response.status_code == 400 and "status" in payload and "Could not find the 'status' column of 'crops'" in response.text:
        payload.pop("status")
        response = requests.patch(
            rest_url("crops"),
            headers=supabase_headers("return=representation"),
            params={"id": f"eq.{crop_id}"},
            json=payload,
            timeout=10,
        )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else payload


def delete_crop(crop_id):
    response = requests.delete(
        rest_url("crops"),
        headers=supabase_headers(),
        params={"id": f"eq.{crop_id}"},
        timeout=10,
    )
    response.raise_for_status()
    return True


def create_farm(farm_name, location, size, fields, status="active"):
    payload = {
        "farm_name": farm_name,
        "location": location,
        "size": float(size),
        "fields": int(fields),
        "status": str(status or "active").lower(),
    }
    response = requests.post(
        rest_url("farms"),
        headers=supabase_headers("return=representation"),
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else payload


def update_farm(farm_id, farm_name=None, location=None, size=None, fields=None, status=None):
    payload = {}
    if farm_name is not None:
        payload["farm_name"] = farm_name
    if location is not None:
        payload["location"] = location
    if size is not None:
        payload["size"] = float(size)
    if fields is not None:
        payload["fields"] = int(fields)
    if status is not None:
        payload["status"] = str(status or "active").lower()

    if not payload:
        return None

    response = requests.patch(
        rest_url("farms"),
        headers=supabase_headers("return=representation"),
        params={"id": f"eq.{farm_id}"},
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else payload


def delete_farm(farm_id):
    response = requests.delete(
        rest_url("farms"),
        headers=supabase_headers(),
        params={"id": f"eq.{farm_id}"},
        timeout=10,
    )
    response.raise_for_status()
    return True


def update_user(user_id, name=None, email=None, role=None, password_hash=None, status=None, farm_name=None):
    payload = {}
    if name is not None:
        payload["name"] = name
    if email is not None:
        payload["email"] = email
    if role is not None:
        payload["role"] = str(role or "user").strip().lower()
    if password_hash is not None:
        payload["password_hash"] = password_hash
    if status is not None:
        payload["status"] = str(status or "active").strip().lower()
    if farm_name is not None:
        payload["farm_name"] = farm_name

    if not payload:
        return find_user_by_id(user_id)

    response = requests.patch(
        rest_url("users"),
        headers=supabase_headers("return=representation"),
        params={"id": f"eq.{user_id}"},
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else find_user_by_id(user_id)


def delete_user(user_id):
    response = requests.delete(
        rest_url("users"),
        headers=supabase_headers(),
        params={"id": f"eq.{user_id}"},
        timeout=10,
    )
    response.raise_for_status()
    return True


def create_harvest_submission(
    submitted_by,
    crop,
    region,
    planting_date,
    expected_harvest_start,
    expected_harvest_end,
    calendar_code=None,
    model_window=None,
    notes=None,
    status="submitted",
    user_id=None,
):
    payload = {
        "submitted_by": submitted_by or "Unknown Farmer",
        "crop": crop,
        "region": region,
        "planting_date": planting_date,
        "expected_harvest_start": expected_harvest_start,
        "expected_harvest_end": expected_harvest_end,
        "calendar_code": calendar_code,
        "model_window": model_window or {},
        "notes": notes,
        "status": str(status or "submitted").strip().lower(),
    }
    if user_id is not None:
        payload["user_id"] = user_id

    response = requests.post(
        rest_url("harvest_submissions"),
        headers=supabase_headers("return=representation"),
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else payload


def get_harvest_submissions(user_id=None):
    params = {"select": "*", "order": "created_at.desc"}
    if user_id is not None:
        params["user_id"] = f"eq.{user_id}"

    response = requests.get(
        rest_url("harvest_submissions"),
        headers=supabase_headers(),
        params=params,
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


def create_audit_log(user_name, activity, affected_record, status="success", activity_date=None, activity_time=None):
    payload = {
        "user_name": user_name,
        "activity": activity,
        "affected_record": affected_record,
        "status": str(status or "success").strip().lower(),
        "activity_date": activity_date or datetime.now().date().isoformat(),
        "activity_time": activity_time or datetime.now().time().strftime("%H:%M"),
    }

    response = requests.post(
        rest_url("audit_logs"),
        headers=supabase_headers("return=representation"),
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else payload


def get_all_auth_events():
    response = requests.get(
        rest_url("auth_events"),
        headers=supabase_headers(),
        params={"select": "*", "order": "created_at.desc"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_all_audit_logs():
    rows = []

    try:
        response = requests.get(
            rest_url("audit_logs"),
            headers=supabase_headers(),
            params={"select": "*", "order": "created_at.desc"},
            timeout=10,
        )
        response.raise_for_status()
        rows.extend(response.json() or [])
    except Exception:
        pass

    try:
        auth_rows = get_all_auth_events()
    except Exception:
        auth_rows = []

    for row in auth_rows:
        created_at = row.get("created_at") or datetime.utcnow().isoformat() + "Z"
        event_type = str(row.get("event_type") or "login").strip().lower()
        success = bool(row.get("success"))

        if event_type == "login":
            event_label = "Login succeeded" if success else "Login failed"
        elif event_type == "logout":
            event_label = "Logged out" if success else "Logout failed"
        elif event_type == "register":
            event_label = "Account registered" if success else "Registration failed"
        else:
            event_label = event_type.replace("_", " ").title()

        details = row.get("details") or {}
        email = row.get("email")
        user_id = row.get("user_id")
        user_name = email or "System"
        affected_record = email or details.get("reason") or "Authentication event"

        if user_id and not email:
            try:
                user = find_user_by_id(user_id)
                if user:
                    affected_record = user.get("email") or affected_record
                    user_name = user.get("name") or user.get("email") or "System"
            except Exception:
                user_name = "System"

        dt_value = created_at
        if dt_value.endswith("Z"):
            dt_value = dt_value[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(dt_value)
            date_value = dt.date().isoformat()
            time_value = dt.strftime("%H:%M")
        except ValueError:
            date_value = str(created_at)[:10]
            time_value = str(created_at)[11:16] if len(str(created_at)) > 10 else "00:00"

        rows.append({
            "user_name": user_name,
            "email": email,
            "activity": event_label,
            "event_type": event_type,
            "details": details,
            "affected_record": affected_record,
            "status": "success" if success else "failed",
            "activity_date": date_value,
            "activity_time": time_value,
            "created_at": created_at,
            "source": "auth_events",
        })

    rows.sort(key=lambda row: str(row.get("created_at") or row.get("activity_date") or ""), reverse=True)
    return rows


def seed_demo_audit_logs():
    try:
        if get_all_audit_logs():
            return get_all_audit_logs()
    except Exception:
        return []

    sample_logs = [
        {
            "user_name": "Sarah Admin",
            "activity": "Account activated",
            "affected_record": "Farmer: Thandi Nkosi",
            "status": "success",
            "activity_date": "2026-10-30",
            "activity_time": "09:14",
        },
        {
            "user_name": "Sarah Admin",
            "activity": "Crop updated",
            "affected_record": "Crop: Maize (maturity 120 days)",
            "status": "success",
            "activity_date": "2026-10-29",
            "activity_time": "15:02",
        },
        {
            "user_name": "Sarah Admin",
            "activity": "Push notification sent",
            "affected_record": "Location: Hammanskraal, Zone 1",
            "status": "success",
            "activity_date": "2026-10-29",
            "activity_time": "08:40",
        },
        {
            "user_name": "Sarah Admin",
            "activity": "Weather threshold changed",
            "affected_record": "Heavy rainfall > 40 mm",
            "status": "success",
            "activity_date": "2026-10-28",
            "activity_time": "11:20",
        },
        {
            "user_name": "Sarah Admin",
            "activity": "Farm deactivated",
            "affected_record": "Farm: HarvestCo Tunnels",
            "status": "success",
            "activity_date": "2026-10-27",
            "activity_time": "16:45",
        },
        {
            "user_name": "Sarah Admin",
            "activity": "Market API sync",
            "affected_record": "Market prices",
            "status": "failed",
            "activity_date": "2026-10-27",
            "activity_time": "06:00",
        },
    ]

    created = []
    for item in sample_logs:
        created.append(create_audit_log(
            item["user_name"],
            item["activity"],
            item["affected_record"],
            item["status"],
            item["activity_date"],
            item["activity_time"],
        ))
    return created


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


def get_all_project_history(limit=None):
    params = {"select": "*", "order": "created_at.desc"}
    if limit is not None:
        params["limit"] = str(limit)

    response = requests.get(
        rest_url("project_history"),
        headers=supabase_headers(),
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
