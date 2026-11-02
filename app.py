from functools import wraps
import json
import logging
import os
import re
import time
import warnings
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from hmac import compare_digest
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import (
    DuplicateUserError,
    create_audit_log,
    create_crop,
    create_farm,
    create_harvest_submission,
    create_irrigation_log,
    create_user,
    delete_crop,
    delete_farm,
    delete_user,
    find_user_by_email,
    find_user_by_id,
    get_all_crops,
    get_all_farms,
    get_all_irrigation_logs,
    get_all_users,
    get_harvest_submissions,
    get_user_history,
    init_db,
    record_auth_event,
    update_crop,
    update_farm,
    update_user,
)


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
APPLICATION_PREFIX = os.environ.get("APPLICATION_PREFIX", "").strip()
MARKET_CACHE_SECONDS = 15 * 60
MARKET_CACHE = {"expires_at": 0, "data": None}
PUSH_NOTIFICATIONS = []
ALERTS = [
    {
        "id": 1,
        "title": "[Admin] heat today",
        "message": "please water plants",
        "category": "Crop Disease",
        "severity": "High",
        "related": "-",
        "user": "John Mokoena",
        "when": "Just now",
        "status": "Open",
    },
    {
        "id": 2,
        "title": "Early blight risk rising",
        "message": "Humidity above 80% for 3 days in Field B. Inspect tomato leaves.",
        "category": "Crop Disease",
        "severity": "High",
        "related": "Field B",
        "user": "John Mokoena",
        "when": "2h ago",
        "status": "Open",
    },
    {
        "id": 3,
        "title": "Low soil moisture",
        "message": "Field A moisture dropped to 18%. Irrigation recommended within 24h.",
        "category": "Irrigation",
        "severity": "High",
        "related": "Field A",
        "user": "John Mokoena",
        "when": "5h ago",
        "status": "Open",
    },
    {
        "id": 4,
        "title": "Rain expected Thursday",
        "message": "12mm rainfall forecast. Consider postponing irrigation.",
        "category": "Weather",
        "severity": "Medium",
        "related": "-",
        "user": "John Mokoena",
        "when": "1d ago",
        "status": "Open",
    },
    {
        "id": 5,
        "title": "Maize harvest window",
        "message": "Field C maize reaches maturity in 9 days.",
        "category": "Harvests",
        "severity": "Low",
        "related": "Field C",
        "user": "John Mokoena",
        "when": "2d ago",
        "status": "Reviewed",
    },
    {
        "id": 6,
        "title": "Soil pH below target",
        "message": "Field D pH 5.4. Apply agricultural lime.",
        "category": "Soil Conditions",
        "severity": "Medium",
        "related": "Field D",
        "user": "John Mokoena",
        "when": "3d ago",
        "status": "Resolved",
    },
]
WEATHER_CACHE_SECONDS = 10 * 60
WEATHER_CACHE = {}


def add_audit_log(user_name, activity, affected_record, status="success"):
    try:
        create_audit_log(
            user_name or "System",
            activity,
            affected_record,
            status,
            datetime.now().date().isoformat(),
            datetime.now().time().strftime("%H:%M"),
        )
    except Exception:
        pass


DEFAULT_FIELD_NAME = os.environ.get("DEFAULT_FIELD_NAME", "Field A")
DEFAULT_FIELD_LATITUDE = float(os.environ.get("DEFAULT_FIELD_LATITUDE", "-25.7479"))
DEFAULT_FIELD_LONGITUDE = float(os.environ.get("DEFAULT_FIELD_LONGITUDE", "28.2293"))
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "").strip()
FARMAZONE_URL = "https://farmazone.co.za/produce/"
FARMAZONE_CACHE = {"expires_at": 0, "data": None}
SOUTH_AFRICAN_FARMING_AREAS = [
    {"name": "Hammanskraal", "lat": -25.4062, "lon": 28.2866},
    {"name": "Pretoria", "lat": -25.7479, "lon": 28.2293},
    {"name": "Johannesburg", "lat": -26.2041, "lon": 28.0473},
    {"name": "Polokwane", "lat": -23.8962, "lon": 29.4486},
    {"name": "Mbombela", "lat": -25.4745, "lon": 30.9695},
    {"name": "Bloemfontein", "lat": -29.0852, "lon": 26.1596},
    {"name": "Durban", "lat": -29.8587, "lon": 31.0218},
    {"name": "Cape Town", "lat": -33.9249, "lon": 18.4241},
]

SOUTH_AFRICAN_MARKET_DATA = [
    {"produce": "Potatoes", "type": "Vegetable", "min": 4.0, "average": 7.0, "max": 11.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Tomatoes", "type": "Vegetable", "min": 6.0, "average": 12.0, "max": 20.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Onions", "type": "Vegetable", "min": 4.0, "average": 8.0, "max": 13.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Cabbage", "type": "Vegetable", "min": 6.0, "average": 11.0, "max": 18.0, "unit": "per head", "market": "Johburg Market"},
    {"produce": "Carrots", "type": "Vegetable", "min": 4.0, "average": 7.0, "max": 12.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Spinach", "type": "Vegetable", "min": 5.0, "average": 9.0, "max": 14.0, "unit": "per bunch", "market": "Johburg Market"},
    {"produce": "Green peppers", "type": "Vegetable", "min": 10.0, "average": 18.0, "max": 30.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Butternut", "type": "Vegetable", "min": 3.0, "average": 6.0, "max": 10.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Maize (green)", "type": "Vegetable", "min": 2.0, "average": 4.0, "max": 7.0, "unit": "per cob", "market": "Johburg Market"},
    {"produce": "Bananas", "type": "Fruit", "min": 8.0, "average": 13.0, "max": 19.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Apples", "type": "Fruit", "min": 8.0, "average": 14.0, "max": 22.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Oranges", "type": "Fruit", "min": 4.0, "average": 8.0, "max": 13.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Avocados", "type": "Fruit", "min": 15.0, "average": 28.0, "max": 45.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Lemons", "type": "Fruit", "min": 6.0, "average": 11.0, "max": 18.0, "unit": "per kg", "market": "Johburg Market"},
    {"produce": "Table grapes", "type": "Fruit", "min": 18.0, "average": 30.0, "max": 48.0, "unit": "per kg", "market": "Johburg Market"},
]

FARMAZONE_FALLBACK = {
    "success": True,
    "indicators": [
        {
            "name": "Potato (Class 1)",
            "market": "JHB · DBN · CPT Floors",
            "price_display": "R42.50 - R48.00 / 10kg",
        },
        {
            "name": "Onions (Class 1)",
            "market": "Consolidated National Average",
            "price_display": "R92.00 - R98.00 / 10kg",
        },
        {
            "name": "White Maize (SAFEX Spot)",
            "market": "Closing Daily Contract",
            "price_display": "R3,485.00 / Ton",
        },
    ],
    "stats": [
        {"value": "50K+", "label": "Daily Log Updates"},
        {"value": "16", "label": "Monitored Terminals"},
        {"value": "0.0ms", "label": "Query Overhead"},
    ],
    "updated_at": "Farmazone sample indicators",
    "source": "Farmazone",
    "source_url": FARMAZONE_URL,
    "errors": [],
}

MARKET_SYMBOLS = [
    {
        "name": "Corn",
        "symbol": "ZC=F",
        "unit": "USD / bushel",
        "scale": 0.01,
        "source": "CBOT delayed futures",
    },
    {
        "name": "Wheat",
        "symbol": "ZW=F",
        "unit": "USD / bushel",
        "scale": 0.01,
        "source": "CBOT delayed futures",
    },
    {
        "name": "Soybean",
        "symbol": "ZS=F",
        "unit": "USD / bushel",
        "scale": 0.01,
        "source": "CBOT delayed futures",
    },
    {
        "name": "Oats",
        "symbol": "ZO=F",
        "unit": "USD / bushel",
        "scale": 0.01,
        "source": "CBOT delayed futures",
    },
    {
        "name": "Coffee",
        "symbol": "KC=F",
        "unit": "USD / lb",
        "scale": 0.01,
        "source": "ICE delayed futures",
    },
    {
        "name": "Sugar",
        "symbol": "SB=F",
        "unit": "USD / lb",
        "scale": 0.01,
        "source": "ICE delayed futures",
    },
]


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-smart-agriculture-key")
    harvest_calendar_path = Path(os.environ.get("HARVEST_DATA_PATH", os.path.join(BASE_DIR, "crop_calendar_gaul_asap1.csv")))
    harvest_model_path = Path(os.environ.get("HARVEST_MODEL_PATH", os.path.join(BASE_DIR, "south_africa_harvest_window_model.pkl")))
    try:
        app.config["HARVEST_CALENDAR"] = load_harvest_calendar(harvest_calendar_path)
    except Exception as exc:
        print(f"Warning: Harvest calendar could not be loaded: {exc}")
        app.config["HARVEST_CALENDAR"] = pd.DataFrame()
    try:
        app.config["HARVEST_MODEL"] = load_harvest_model(harvest_model_path)
    except Exception as exc:
        print(f"Warning: Harvest model could not be loaded: {exc}")
        app.config["HARVEST_MODEL"] = None

    @app.context_processor
    def inject_application_prefix():
        return {"application_prefix": APPLICATION_PREFIX}

    init_db()
    register_project_blueprints(app)

    @app.before_request
    def protect_project_apis():
        protected_prefixes = (
            "/api/crop_disease_detection",
            "/api/soil_classification",
            "/api/irrigation_prediction",
        )
        if request.path.startswith(protected_prefixes) and not get_current_session_user():
            return jsonify({"error": "Please log in to use this project."}), 401

    @app.route("/")
    def landing():
        return render_template("landing.html")

    @app.route("/api/market-prices")
    def market_prices():
        market_data = []
        for item in get_market_management_data():
            min_price = item.get("min")
            average_price = item.get("average")
            max_price = item.get("max")
            market_name = item.get("market") or "Johburg Market"
            product_type = item.get("type") or "Vegetable"
            unit = item.get("unit") or "per kg"

            def as_float(value, fallback=0.0):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float(fallback)

            min_float = as_float(min_price)
            average_float = as_float(average_price, (min_float + as_float(max_price)) / 2 if max_price is not None else min_float)
            max_float = as_float(max_price, min_float)
            market_data.append({
                "produce": item.get("produce") or "Produce",
                "type": product_type,
                "market": market_name,
                "currency": "ZAR",
                "unit": unit,
                "min": round(min_float, 2),
                "average": round(average_float, 2),
                "max": round(max_float, 2),
                "price_range": {
                    "min": round(min_float, 2),
                    "average": round(average_float, 2),
                    "max": round(max_float, 2),
                    "currency": "ZAR",
                    "display": f"R {min_float:.2f} - R {max_float:.2f}",
                },
                "range_display": f"R {min_float:.2f} - R {max_float:.2f} / {unit}",
            })

        return jsonify({
            "success": True,
            "source": "South Africa produce market pricing",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "market_data": market_data,
            "count": len(market_data),
        })

    @app.route("/api/market-management")
    def market_management_api():
        return market_prices()

    @app.route("/api/farmazone-market")
    def farmazone_market():
        return jsonify(get_farmazone_market_data())

    @app.route("/api/weather-summary")
    def weather_summary():
        return jsonify(
            get_weather_summary(
                request.args.get("lat"),
                request.args.get("lon"),
                request.args.get("field"),
            )
        )

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not name or not email or not password:
                flash("Please complete all required fields.", "error")
                return render_template("register.html")

            if password != confirm_password:
                flash("Passwords do not match.", "error")
                return render_template("register.html")

            try:
                user = create_user(name, email, generate_password_hash(password), "user")
                save_auth_event(
                    user.get("id"),
                    email,
                    "register",
                    True,
                    {"role": user.get("role", "user")},
                )
                add_audit_log(name, "Account registered", f"Email: {email}", "success")
                flash("Account created. You can now log in.", "success")
                return redirect(url_for("login"))
            except DuplicateUserError:
                save_auth_event(None, email, "register", False, {"reason": "duplicate_email"})
                add_audit_log(name or email, "Account registration failed", f"Email: {email}", "failed")
                flash("An account with that email already exists.", "error")

        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            user = find_user_by_email(email)
            if not user or not verify_password_hash(user.get("password_hash"), password):
                save_auth_event(
                    user.get("id") if user else None,
                    email,
                    "login",
                    False,
                    {"reason": "invalid_credentials"},
                )
                add_audit_log(user.get("name") if user else email, "Login attempt failed", f"Email: {email}", "failed")
                flash("Invalid email or password.", "error")
                return render_template("login.html")

            normalized_role = str(user.get("role") or "user").strip().lower()
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = normalized_role
            save_auth_event(user["id"], email, "login", True, {"role": normalized_role})
            add_audit_log(user.get("name") or "System", "Logged in to the system", f"Role: {normalized_role}", "success")

            if normalized_role == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("user_dashboard"))

        return render_template("login.html")

    @app.route("/dashboard")
    @login_required
    def user_dashboard():
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        history_items = safe_get_user_history(session["user_id"], limit=5)
        return render_template("user_dashboard.html", history_items=history_items)

    @app.route("/dashboard/crop-disease")
    @login_required
    def crop_disease_page():
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return render_template("crop_disease.html")

    @app.route("/dashboard/soil-classification")
    @login_required
    def soil_classification_page():
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return render_template("soil_classification.html")

    @app.route("/dashboard/irrigation")
    @login_required
    def irrigation_page():
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return render_template("irrigation.html")

    @app.route("/dashboard/irrigation-prediction")
    @login_required
    def irrigation_prediction_page():
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return render_template("irrigation_prediction.html")

    @app.route("/dashboard/harvest-prediction", methods=["GET", "POST"])
    @login_required
    def harvest_prediction_page():
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))

        frame = app.config.get("HARVEST_CALENDAR")
        crops = []
        regions = []
        selected_crop = (request.form.get("crop") or request.args.get("crop") or "").strip()
        selected_region = (request.form.get("region") or request.args.get("region") or "").strip()

        if isinstance(frame, pd.DataFrame) and not frame.empty:
            crops = sorted(frame["crop"].dropna().unique().tolist())
            if selected_crop in crops:
                regions = sorted(frame.loc[frame["crop"] == selected_crop, "name1_shr"].dropna().unique().tolist())
            elif frame.empty is False:
                regions = sorted(frame["name1_shr"].dropna().unique().tolist())

        prediction = None
        analysis = None
        if request.method == "POST":
            crop = (request.form.get("crop") or "").strip()
            region = (request.form.get("region") or "").strip()
            planting_date = (request.form.get("planting_date") or "").strip()
            action = (request.form.get("action") or "predict").strip()

            if crop and region and planting_date:
                prediction = build_harvest_prediction(
                    app.config.get("HARVEST_CALENDAR"),
                    app.config.get("HARVEST_MODEL"),
                    crop,
                    region,
                    planting_date,
                )
                if prediction.get("success"):
                    try:
                        create_harvest_submission(
                            submitted_by=session.get("name") or "Unknown Farmer",
                            user_id=session.get("user_id"),
                            crop=crop,
                            region=region,
                            planting_date=planting_date,
                            expected_harvest_start=prediction["harvest_window"]["start"],
                            expected_harvest_end=prediction["harvest_window"]["end"],
                            calendar_code=prediction.get("calendar_code"),
                            model_window=prediction.get("model_window"),
                            notes=prediction.get("note"),
                            status="submitted",
                        )
                        add_audit_log(session.get("name") or "User", "Submitted harvest prediction", f"{crop} / {region}", "success")
                    except Exception:
                        pass
                    if action == "generate_analysis":
                        analysis = generate_harvest_analysis(prediction)
                else:
                    flash(prediction.get("error", "Harvest prediction failed."), "error")
            else:
                flash("Crop, region, and planting date are required.", "error")

        submissions = []
        try:
            submissions = get_harvest_submissions(session.get("user_id"))[:5]
        except Exception:
            submissions = []

        return render_template(
            "harvest_prediction.html",
            crops=crops,
            regions=regions,
            selected_crop=selected_crop,
            selected_region=selected_region,
            prediction=prediction,
            analysis=analysis,
            submissions=submissions,
        )

    @app.route("/dashboard/history")
    @login_required
    def history_page():
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        history_items = decorate_history_items(safe_get_user_history(session["user_id"]))
        return render_template("history.html", history_items=history_items)

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        users = get_all_users()
        return render_template("admin_dashboard.html", users=users)

    @app.route("/admin/users")
    @admin_required
    def admin_users():
        users = get_all_users()
        return render_template("admin_users.html", users=users)

    @app.route("/admin/crops")
    @admin_required
    def admin_crops():
        crops = get_all_crops()
        return render_template("admin_crops.html", crops=crops)

    @app.route("/admin/weather")
    @admin_required
    def admin_weather():
        default_area = request.args.get("area") or "Hammanskraal"
        locations = []
        for area in SOUTH_AFRICAN_FARMING_AREAS:
            summary = get_weather_summary(area["lat"], area["lon"], area["name"])
            locations.append({
                "name": area["name"],
                "temperature": summary.get("temperature") if isinstance(summary.get("temperature"), (int, float)) else 0,
                "temperature_display": summary.get("temperature_display") or "-- C",
                "condition": summary.get("condition") or "Weather update",
                "humidity": summary.get("humidity") if isinstance(summary.get("humidity"), (int, float)) else 0,
                "wind_speed": summary.get("wind_speed") if isinstance(summary.get("wind_speed"), (int, float)) else 0,
                "forecast": summary.get("forecast") or [],
                "source": summary.get("source") or "Open-Meteo",
                "updated_at": summary.get("updated_at") or "--",
            })

        selected = next((area for area in locations if area["name"] == default_area), locations[0]) if locations else {}
        return render_template("admin_weather.html", locations=locations, selected=selected)

    @app.route("/admin/irrigation")
    @admin_required
    def admin_irrigation():
        try:
            irrigation_logs = get_all_irrigation_logs()
        except Exception:
            irrigation_logs = []

        normalized_logs = []
        for row in irrigation_logs:
            status_value = str(row.get("status") or "adequate").strip().lower()
            normalized_logs.append({
                "id": row.get("id"),
                "farmer_name": row.get("farmer_name") or "Unknown farmer",
                "farm_name": row.get("farm_name") or "Unassigned farm",
                "crop_name": row.get("crop_name") or "Unknown crop",
                "variety": row.get("variety") or "General",
                "location": row.get("location") or "Not specified",
                "stage": row.get("stage") or "Unknown",
                "soil_type": row.get("soil_type") or "Unknown",
                "moisture_percent": row.get("moisture_percent"),
                "temperature_c": row.get("temperature_c"),
                "humidity_percent": row.get("humidity_percent"),
                "rain_3d_mm": row.get("rain_3d_mm"),
                "last_watered": row.get("last_watered") or "--",
                "status": status_value,
                "recommendation": row.get("recommendation") or "Monitor schedule",
                "updated_at": row.get("updated_at") or "--",
            })

        humidity_values = [float(log["humidity_percent"]) for log in normalized_logs if isinstance(log["humidity_percent"], (int, float))]
        moisture_values = [float(log["moisture_percent"]) for log in normalized_logs if isinstance(log["moisture_percent"], (int, float))]
        avg_humidity = round(sum(humidity_values) / len(humidity_values), 1) if humidity_values else 0
        avg_soil_moisture = round(sum(moisture_values) / len(moisture_values), 1) if moisture_values else 0
        low_moisture = sum(1 for log in normalized_logs if log["status"] == "low soil moisture")

        return render_template(
            "admin_irrigation.html",
            irrigation_logs=normalized_logs,
            total_records=len(normalized_logs),
            avg_humidity=avg_humidity,
            avg_soil_moisture=avg_soil_moisture,
            low_moisture=low_moisture,
        )

    @app.route("/admin/market")
    @admin_required
    def admin_market():
        selected_market = request.args.get("market") or "Johburg Market"
        selected_type = request.args.get("type") or "All"
        search = (request.args.get("search") or "").strip().lower()

        market_items = get_market_management_data()
        if selected_market and selected_market != "All":
            market_items = [item for item in market_items if item.get("market") == selected_market]
        if selected_type and selected_type != "All":
            market_items = [item for item in market_items if str(item.get("type") or "").lower() == selected_type.lower()]
        if search:
            market_items = [
                item for item in market_items
                if search in str(item.get("produce") or "").lower()
                or search in str(item.get("type") or "").lower()
            ]

        market_names = ["All"] + sorted({item.get("market") for item in market_items if item.get("market")})
        type_names = ["All"] + sorted({str(item.get("type") or "") for item in market_items if item.get("type")})
        return render_template(
            "admin_market.html",
            market_items=market_items,
            market_names=market_names,
            type_names=type_names,
            selected_market=selected_market,
            selected_type=selected_type,
            search=search,
        )

    @app.route("/admin/push-notifications", methods=["GET", "POST"])
    @admin_required
    def admin_push_notifications():
        farms = get_all_farms()
        farm_names = [farm.get("farm_name") for farm in farms if farm.get("farm_name")]
        status_filter = (request.args.get("status") or "All statuses").strip()

        if request.method == "POST":
            title = (request.form.get("title") or "").strip()
            message = (request.form.get("message") or "").strip()
            location = (request.form.get("location") or "All locations").strip()
            category = (request.form.get("category") or "Weather").strip()
            priority = (request.form.get("priority") or "Medium").strip()

            if not title or not message:
                flash("Title and message are required.", "error")
                return render_template(
                    "admin_push_notifications.html",
                    notifications=PUSH_NOTIFICATIONS,
                    farms=farm_names,
                    selected_location=location,
                    selected_category=category,
                    selected_priority=priority,
                    status_filter=status_filter,
                )

            alert_id = max([item.get("id", 0) for item in ALERTS] + [0]) + 1
            alert_item = {
                "id": alert_id,
                "title": title,
                "message": message,
                "category": category,
                "severity": priority,
                "related": location if location != "All locations" else "-",
                "user": "John Mokoena",
                "when": "Just now",
                "status": "Open",
            }
            ALERTS.insert(0, alert_item)
            push_item = {
                "title": title,
                "message": message,
                "location": location,
                "category": category,
                "priority": priority,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "status": "Open",
                "alert_id": alert_id,
            }
            PUSH_NOTIFICATIONS.insert(0, push_item)
            add_audit_log(
                session.get("name") or "Admin",
                "Sent push notification",
                f"Title: {title} | Location: {location}",
                "success",
            )
            flash("Push notification sent successfully.", "success")
            return redirect(url_for("admin_push_notifications"))

        notifications = list(PUSH_NOTIFICATIONS)
        if status_filter and status_filter != "All statuses":
            if status_filter == "Unresolved":
                notifications = [
                    item for item in notifications
                    if str(item.get("status") or "Open").lower() != "resolved"
                ]
            else:
                notifications = [
                    item for item in notifications
                    if str(item.get("status") or "Open").lower() == status_filter.lower()
                ]

        return render_template(
            "admin_push_notifications.html",
            notifications=notifications,
            farms=farm_names,
            selected_location="All locations",
            selected_category="Weather",
            selected_priority="Medium",
            status_filter=status_filter,
            unresolved_alerts=[item for item in ALERTS if str(item.get("status") or "").lower() != "resolved"],
        )

    @app.route("/admin/alerts", methods=["GET", "POST"])
    @admin_required
    def admin_alerts():
        if request.method == "POST":
            action = (request.form.get("action") or "").strip().lower()
            alert_id = request.form.get("alert_id")
            new_status = "Resolved" if action == "resolve_alert" else "Reviewed"
            if alert_id is not None:
                for item in ALERTS:
                    if str(item.get("id")) == str(alert_id):
                        item["status"] = new_status
                        break
                for item in PUSH_NOTIFICATIONS:
                    if str(item.get("alert_id")) == str(alert_id):
                        item["status"] = new_status
                        break
            add_audit_log(
                session.get("name") or "Admin",
                "Updated alert status",
                f"Alert ID: {alert_id} | Status: {new_status}",
                "success" if new_status != "Reviewed" else "reviewed",
            )
            flash("Alert updated successfully.", "success")
            return redirect(url_for("admin_alerts"))

        search_term = (request.args.get("search") or "").strip().lower()
        category_filter = (request.args.get("category") or "All categories").strip()
        severity_filter = (request.args.get("severity") or "All severities").strip()
        status_filter = (request.args.get("status") or "All statuses").strip()

        alerts = list(ALERTS)
        if search_term:
            alerts = [
                item for item in alerts
                if search_term in str(item.get("title") or "").lower()
                or search_term in str(item.get("message") or "").lower()
                or search_term in str(item.get("category") or "").lower()
                or search_term in str(item.get("user") or "").lower()
            ]
        if category_filter and category_filter != "All categories":
            alerts = [
                item for item in alerts
                if str(item.get("category") or "").lower() == category_filter.lower()
            ]
        if severity_filter and severity_filter != "All severities":
            alerts = [
                item for item in alerts
                if str(item.get("severity") or "").lower() == severity_filter.lower()
            ]
        if status_filter and status_filter != "All statuses":
            alerts = [
                item for item in alerts
                if str(item.get("status") or "").lower() == status_filter.lower()
            ]

        return render_template(
            "admin_alerts.html",
            alerts=alerts,
            search_term=search_term,
            category_filter=category_filter,
            severity_filter=severity_filter,
            status_filter=status_filter,
        )

    @app.route("/admin/audit-logs")
    @admin_required
    def admin_audit_logs():
        try:
            logs = get_all_audit_logs()
        except Exception:
            logs = []

        search_term = (request.args.get("search") or "").strip().lower()
        status_filter = (request.args.get("status") or "All statuses").strip()

        filtered_logs = logs
        if search_term:
            filtered_logs = [
                row for row in filtered_logs
                if search_term in str(row.get("user_name") or "").lower()
                or search_term in str(row.get("activity") or "").lower()
                or search_term in str(row.get("affected_record") or "").lower()
            ]
        if status_filter and status_filter != "All statuses":
            filtered_logs = [
                row for row in filtered_logs
                if str(row.get("status") or "success").lower() == status_filter.lower()
            ]

        return render_template("admin_audit_logs.html", audit_logs=filtered_logs, search=search_term, status_filter=status_filter)

    @app.route("/admin/farms")
    @admin_required
    def admin_farms():
        farms = get_all_farms()
        return render_template("admin_farms.html", farms=farms)

    @app.route("/admin/crops/create", methods=["POST"])
    @admin_required
    def admin_create_crop():
        crop_name = request.form.get("crop_name", "").strip()
        varieties = request.form.get("varieties", "").strip()
        maturity_period = request.form.get("maturity_period", "").strip()
        growth_stage = request.form.get("growth_stage", "").strip()
        supported_disease_category = request.form.get("supported_disease_category", "").strip()
        status = (request.form.get("status") or "active").strip().lower()

        if not crop_name:
            flash("Crop name is required.", "error")
            return redirect(url_for("admin_crops"))

        try:
            create_crop(crop_name, varieties, maturity_period, growth_stage, supported_disease_category, status=status)
            add_audit_log(session.get("name") or "Admin", "Created crop record", crop_name, "success")
            flash("Crop created successfully.", "success")
        except Exception as exc:
            add_audit_log(session.get("name") or "Admin", "Failed to create crop record", crop_name, "failed")
            flash(f"Could not create crop: {exc}", "error")

        return redirect(url_for("admin_crops"))

    @app.route("/admin/crops/<crop_id>/update", methods=["POST"])
    @admin_required
    def admin_update_crop(crop_id):
        crop_name = request.form.get("crop_name", "").strip()
        varieties = request.form.get("varieties", "").strip()
        maturity_period = request.form.get("maturity_period", "").strip()
        growth_stage = request.form.get("growth_stage", "").strip()
        supported_disease_category = request.form.get("supported_disease_category", "").strip()
        status = (request.form.get("status") or "active").strip().lower()

        try:
            update_crop(
                crop_id,
                crop_name=crop_name or None,
                varieties=varieties or None,
                maturity_period=maturity_period or None,
                growth_stage=growth_stage or None,
                supported_disease_category=supported_disease_category or None,
                status=status,
            )
            add_audit_log(session.get("name") or "Admin", "Updated crop record", crop_name or crop_id, "success")
            flash("Crop updated successfully.", "success")
        except Exception as exc:
            add_audit_log(session.get("name") or "Admin", "Failed to update crop record", crop_id, "failed")
            flash(f"Could not update crop: {exc}", "error")

        return redirect(url_for("admin_crops"))

    @app.route("/admin/crops/<crop_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_crop(crop_id):
        try:
            delete_crop(crop_id)
            add_audit_log(session.get("name") or "Admin", "Deleted crop record", crop_id, "success")
            flash("Crop deleted successfully.", "success")
        except Exception as exc:
            add_audit_log(session.get("name") or "Admin", "Failed to delete crop record", crop_id, "failed")
            flash(f"Could not delete crop: {exc}", "error")

        return redirect(url_for("admin_crops"))

    @app.route("/admin/users/create", methods=["POST"])
    @admin_required
    def admin_create_user():
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = (request.form.get("role") or "user").strip().lower()
        farm_name = request.form.get("farm_name", "").strip()
        status = (request.form.get("status") or "active").strip().lower()

        if not name or not email or not password:
            flash("Name, email, and password are required.", "error")
            return redirect(url_for("admin_dashboard"))

        try:
            create_user(name, email, generate_password_hash(password), role, status=status, farm_name=farm_name or None)
            add_audit_log(session.get("name") or "Admin", "Created user account", f"{name} ({email})", "success")
            flash("User created successfully.", "success")
        except DuplicateUserError:
            add_audit_log(session.get("name") or "Admin", "Failed to create user account", f"{name} ({email})", "failed")
            flash("A user with that email already exists.", "error")
        except Exception as exc:
            add_audit_log(session.get("name") or "Admin", "Failed to create user account", f"{name} ({email})", "failed")
            flash(f"Could not create user: {exc}", "error")

        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/users/<user_id>/update", methods=["POST"])
    @admin_required
    def admin_update_user(user_id):
        user = find_user_by_id(user_id)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for("admin_dashboard"))

        name = request.form.get("name", "").strip() or user["name"]
        email = request.form.get("email", "").strip().lower() or user["email"]
        role = (request.form.get("role") or user.get("role") or "user").strip().lower()
        status = (request.form.get("status") or user.get("status") or "active").strip().lower()
        farm_name = request.form.get("farm_name", "").strip()
        password = request.form.get("password", "").strip()

        try:
            payload = {"name": name, "email": email, "role": role, "status": status, "farm_name": farm_name or user.get("farm_name") or None}
            if password:
                payload["password_hash"] = generate_password_hash(password)
            update_user(user_id, **payload)
            add_audit_log(session.get("name") or "Admin", "Updated user account", f"{name} ({email})", "success")
            flash("User updated successfully.", "success")
        except DuplicateUserError:
            add_audit_log(session.get("name") or "Admin", "Failed to update user account", f"{name} ({email})", "failed")
            flash("A user with that email already exists.", "error")
        except Exception as exc:
            add_audit_log(session.get("name") or "Admin", "Failed to update user account", f"{name} ({email})", "failed")
            flash(f"Could not update user: {exc}", "error")

        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/users/<user_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_user(user_id):
        current_user_id = str(session.get("user_id"))
        if str(user_id) == current_user_id:
            flash("You cannot delete your own admin account.", "error")
            return redirect(url_for("admin_dashboard"))

        try:
            delete_user(user_id)
            add_audit_log(session.get("name") or "Admin", "Deleted user account", user_id, "success")
            flash("User deleted successfully.", "success")
        except Exception as exc:
            add_audit_log(session.get("name") or "Admin", "Failed to delete user account", user_id, "failed")
            flash(f"Could not delete user: {exc}", "error")

        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/farms/create", methods=["POST"])
    @admin_required
    def admin_create_farm():
        farm_name = request.form.get("farm_name", "").strip()
        location = request.form.get("location", "").strip()
        size = request.form.get("size", "").strip()
        fields = request.form.get("fields", "1").strip()
        status = (request.form.get("status") or "active").strip().lower()

        if not farm_name or not location or not size:
            flash("Farm name, location, and size are required.", "error")
            return redirect(url_for("admin_farms"))

        try:
            create_farm(farm_name, location, size, fields, status)
            add_audit_log(session.get("name") or "Admin", "Created farm record", farm_name, "success")
            flash("Farm created successfully.", "success")
        except Exception as exc:
            add_audit_log(session.get("name") or "Admin", "Failed to create farm record", farm_name, "failed")
            flash(f"Could not create farm: {exc}", "error")

        return redirect(url_for("admin_farms"))

    @app.route("/admin/farms/<farm_id>/update", methods=["POST"])
    @admin_required
    def admin_update_farm(farm_id):
        farm_name = request.form.get("farm_name", "").strip()
        location = request.form.get("location", "").strip()
        size = request.form.get("size", "").strip()
        fields = request.form.get("fields", "").strip()
        status = (request.form.get("status") or "active").strip().lower()

        try:
            update_farm(
                farm_id,
                farm_name=farm_name or None,
                location=location or None,
                size=float(size) if size else None,
                fields=int(fields) if fields else None,
                status=status,
            )
            add_audit_log(session.get("name") or "Admin", "Updated farm record", farm_name or farm_id, "success")
            flash("Farm updated successfully.", "success")
        except Exception as exc:
            add_audit_log(session.get("name") or "Admin", "Failed to update farm record", farm_id, "failed")
            flash(f"Could not update farm: {exc}", "error")

        return redirect(url_for("admin_farms"))

    @app.route("/admin/farms/<farm_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_farm(farm_id):
        try:
            delete_farm(farm_id)
            add_audit_log(session.get("name") or "Admin", "Deleted farm record", farm_id, "success")
            flash("Farm deleted successfully.", "success")
        except Exception as exc:
            add_audit_log(session.get("name") or "Admin", "Failed to delete farm record", farm_id, "failed")
            flash(f"Could not delete farm: {exc}", "error")

        return redirect(url_for("admin_farms"))

    @app.route("/api/history")
    @login_required
    def history():
        return jsonify({"success": True, "history": safe_get_user_history(session["user_id"])})

    @app.route("/logout")
    def logout():
        user_name = session.get("name") or "User"
        save_auth_event(
            session.get("user_id"),
            None,
            "logout",
            True,
            {"role": session.get("role")},
        )
        add_audit_log(user_name, "Logged out of the system", f"Role: {session.get('role') or 'user'}", "success")
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("landing"))

    return app


def verify_password_hash(stored_hash, password):
    if not stored_hash:
        return False

    try:
        return check_password_hash(stored_hash, password)
    except ValueError:
        if str(stored_hash).startswith("$") and password:
            try:
                import bcrypt
                return bcrypt.checkpw(password.encode("utf-8"), str(stored_hash).encode("utf-8"))
            except Exception:
                return compare_digest(str(stored_hash), password)
        return compare_digest(str(stored_hash), password)


def save_auth_event(user_id, email, event_type, success=True, details=None):
    try:
        return record_auth_event(user_id, email, event_type, success, details)
    except Exception as exc:
        print(f"Warning: Auth event could not be saved: {exc}")
        return None


def safe_get_user_history(user_id, limit=50):
    try:
        return get_user_history(user_id, limit=limit)
    except Exception as exc:
        print(f"Warning: User history could not be loaded: {exc}")
        return []


def decorate_history_items(items):
    return [decorate_history_item(item) for item in items]


def decorate_history_item(item):
    row = dict(item)
    response = row.get("response_payload") or {}
    data = response.get("data") or {}
    image = row.get("upload_public_url") or response.get("image") or data.get("image")

    if image and not str(image).startswith(("http://", "https://", "data:")):
        image = f"data:image/jpeg;base64,{image}"

    row["image_url"] = image
    row["summary"] = history_summary(row, response, data)
    row["request_payload_pretty"] = json.dumps(
        history_display_payload(row.get("request_payload") or {}),
        indent=2,
        ensure_ascii=False,
    )
    row["response_payload_pretty"] = json.dumps(
        history_display_payload(response),
        indent=2,
        ensure_ascii=False,
    )
    return row


def history_display_payload(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key == "image" and isinstance(item, str) and len(item) > 120:
                cleaned[key] = "[image preview shown above]"
            else:
                cleaned[key] = history_display_payload(item)
        return cleaned
    if isinstance(value, list):
        return [history_display_payload(item) for item in value]
    return value


def history_summary(item, response, data):
    project = item.get("project")
    if project == "crop_disease_detection":
        return response.get("label") or "Crop disease result saved"
    if project == "soil_classification":
        soil_type = data.get("soil_type")
        confidence = data.get("confidence")
        if soil_type and confidence is not None:
            return f"{soil_type} soil ({confidence}% confidence)"
        return soil_type or "Soil classification result saved"
    if project == "irrigation_prediction":
        return data.get("prediction") or "Irrigation prediction saved"
    return response.get("response") or "Saved response"


def get_weather_summary(latitude=None, longitude=None, field_name=None):
    lat = parse_coordinate(latitude, DEFAULT_FIELD_LATITUDE)
    lon = parse_coordinate(longitude, DEFAULT_FIELD_LONGITUDE)
    field = (field_name or DEFAULT_FIELD_NAME).strip() or DEFAULT_FIELD_NAME
    cache_key = (round(lat, 4), round(lon, 4), field)
    now = time.time()

    cached = WEATHER_CACHE.get(cache_key)
    if cached and cached["expires_at"] > now:
        return cached["data"]

    try:
        data = fetch_weatherapi_weather(lat, lon, field)
    except Exception as exc:
        try:
            data = fetch_open_meteo_weather(lat, lon, field)
        except Exception as secondary_exc:
            data = {
                "success": False,
                "field": field,
                "temperature": 24,
                "temperature_display": "24 C",
                "condition": "Weather unavailable",
                "feels_like_display": "-- C",
                "humidity": 68,
                "moisture_display": "Humidity 68%",
                "soil_moisture": None,
                "soil_temperature": None,
                "soil_temperature_display": "-- C",
                "wind_speed": None,
                "wind_display": "Wind -- km/h",
                "wind_direction": None,
                "wind_gusts": None,
                "precipitation": None,
                "rain_today": None,
                "rain_display": "Rain -- mm",
                "precipitation_probability": None,
                "pressure": None,
                "cloud_cover": None,
                "uv_index": None,
                "evapotranspiration": None,
                "sunrise": "--",
                "sunset": "--",
                "summary_cards": [],
                "forecast": [],
                "updated_at": "Weather fallback unavailable",
                "source": "Weather fallback unavailable",
                "source_url": "https://www.weatherapi.com/",
                "errors": [str(exc), str(secondary_exc)],
            }

    WEATHER_CACHE[cache_key] = {
        "expires_at": now + WEATHER_CACHE_SECONDS,
        "data": data,
    }
    return data


def parse_coordinate(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_weatherapi_weather(latitude, longitude, field_name):
    if not WEATHER_API_KEY:
        return fetch_open_meteo_weather(latitude, longitude, field_name)

    params = {
        "key": WEATHER_API_KEY,
        "q": f"{latitude},{longitude}",
        "days": 3,
        "aqi": "no",
        "alerts": "no",
    }
    url = f"https://api.weatherapi.com/v1/forecast.json?{urlencode(params)}"
    weather_request = Request(url, headers={"User-Agent": "SmartAgriculture/1.0"})

    with urlopen(weather_request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))

    current = payload.get("current", {})
    forecast_days = payload.get("forecast", {}).get("forecastday", [])
    today = forecast_days[0] if forecast_days else {}
    day = today.get("day", {})
    astro = today.get("astro", {})

    temperature = current.get("temp_c")
    humidity = current.get("humidity")
    precipitation = current.get("precip_mm")
    condition = (current.get("condition") or {}).get("text") or "Weather update"
    rain_today = day.get("totalprecip_mm")
    rain_chance = day.get("daily_chance_of_rain")
    evapotranspiration = day.get("totalprecip_mm")

    summary_cards = [
        {"label": "Condition", "value": condition},
        {"label": "Feels Like", "value": format_celsius(current.get("feelslike_c"))},
        {"label": "Humidity", "value": format_percent(humidity)},
        {"label": "Wind", "value": format_speed(current.get("wind_kph"))},
        {"label": "Wind Gusts", "value": format_speed(current.get("gust_kph"))},
        {"label": "Wind Direction", "value": format_degrees(current.get("wind_degree"))},
        {"label": "Rain Now", "value": format_millimeters(precipitation)},
        {"label": "Rain Today", "value": format_millimeters(rain_today)},
        {"label": "Rain Chance", "value": format_percent(rain_chance)},
        {"label": "Cloud Cover", "value": format_percent(current.get("cloud"))},
        {"label": "Pressure", "value": format_pressure(current.get("pressure_mb"))},
        {"label": "UV Index", "value": format_number(current.get("uv"), 1)},
        {"label": "ET0", "value": format_millimeters(evapotranspiration)},
        {"label": "Sunrise", "value": astro.get("sunrise") or "--"},
        {"label": "Sunset", "value": astro.get("sunset") or "--"},
    ]

    return {
        "success": True,
        "field": field_name,
        "condition": condition,
        "temperature": temperature,
        "temperature_display": format_celsius(temperature),
        "feels_like": current.get("feelslike_c"),
        "feels_like_display": format_celsius(current.get("feelslike_c")),
        "humidity": humidity,
        "moisture_display": f"Humidity {round(humidity)}%" if isinstance(humidity, (int, float)) else "Humidity unavailable",
        "soil_moisture": None,
        "soil_temperature": None,
        "soil_temperature_display": "-- C",
        "wind_speed": current.get("wind_kph"),
        "wind_display": format_speed(current.get("wind_kph")),
        "wind_direction": current.get("wind_degree"),
        "wind_gusts": current.get("gust_kph"),
        "precipitation": precipitation,
        "rain_today": rain_today,
        "rain_display": format_millimeters(rain_today),
        "precipitation_probability": rain_chance,
        "pressure": current.get("pressure_mb"),
        "cloud_cover": current.get("cloud"),
        "uv_index": current.get("uv"),
        "evapotranspiration": evapotranspiration,
        "sunrise": astro.get("sunrise") or "--",
        "sunset": astro.get("sunset") or "--",
        "summary_cards": summary_cards,
        "forecast": build_weatherapi_forecast(forecast_days),
        "updated_at": current.get("last_updated") or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "source": "WeatherAPI live",
        "source_url": "https://www.weatherapi.com/",
        "errors": [],
    }


def fetch_open_meteo_weather(latitude, longitude, field_name):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,weather_code,cloud_cover,pressure_msl,"
            "wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        ),
        "hourly": "soil_moisture_0_to_1cm,soil_temperature_6cm,precipitation_probability",
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,"
            "precipitation_sum,precipitation_probability_max,wind_speed_10m_max,"
            "uv_index_max,et0_fao_evapotranspiration"
        ),
        "forecast_days": 7,
        "timezone": "Africa/Johannesburg",
    }
    url = f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"
    weather_request = Request(url, headers={"User-Agent": "SmartAgriculture/1.0"})

    with urlopen(weather_request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))

    current = payload.get("current", {})
    hourly = payload.get("hourly", {})
    daily = payload.get("daily", {})
    current_hour_index = find_hour_index(hourly.get("time", []), current.get("time"))
    soil_moisture = get_hourly_value(hourly, "soil_moisture_0_to_1cm", current_hour_index)
    soil_temperature = get_hourly_value(hourly, "soil_temperature_6cm", current_hour_index)
    rain_today = get_daily_value(daily, "precipitation_sum")
    temperature = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    apparent_temperature = current.get("apparent_temperature")
    precipitation_probability = get_hourly_value(
        hourly, "precipitation_probability", current_hour_index
    )
    condition = weather_code_label(current.get("weather_code"))
    uv_index = get_daily_value(daily, "uv_index_max")
    evapotranspiration = get_daily_value(daily, "et0_fao_evapotranspiration")

    moisture_display = "Moisture unavailable"
    if isinstance(soil_moisture, (int, float)):
        moisture_display = f"Soil moisture {round(soil_moisture * 100)}%"
    elif isinstance(humidity, (int, float)):
        moisture_display = f"Humidity {round(humidity)}%"

    summary_cards = [
        {"label": "Condition", "value": condition},
        {"label": "Feels Like", "value": format_celsius(apparent_temperature)},
        {"label": "Humidity", "value": format_percent(humidity)},
        {"label": "Wind", "value": format_speed(current.get("wind_speed_10m"))},
        {"label": "Wind Gusts", "value": format_speed(current.get("wind_gusts_10m"))},
        {"label": "Wind Direction", "value": format_degrees(current.get("wind_direction_10m"))},
        {"label": "Rain Now", "value": format_millimeters(current.get("precipitation"))},
        {"label": "Rain Today", "value": format_millimeters(rain_today)},
        {"label": "Rain Chance", "value": format_percent(precipitation_probability)},
        {"label": "Cloud Cover", "value": format_percent(current.get("cloud_cover"))},
        {"label": "Pressure", "value": format_pressure(current.get("pressure_msl"))},
        {"label": "UV Index", "value": format_number(uv_index, 1)},
        {"label": "ET0", "value": format_millimeters(evapotranspiration)},
        {"label": "Sunrise", "value": format_time(get_daily_value(daily, "sunrise"))},
        {"label": "Sunset", "value": format_time(get_daily_value(daily, "sunset"))},
    ]

    return {
        "success": True,
        "field": field_name,
        "condition": condition,
        "temperature": temperature,
        "temperature_display": format_celsius(temperature),
        "feels_like": apparent_temperature,
        "feels_like_display": format_celsius(apparent_temperature),
        "humidity": humidity,
        "moisture_display": moisture_display,
        "soil_moisture": soil_moisture,
        "soil_temperature": soil_temperature,
        "soil_temperature_display": format_celsius(soil_temperature),
        "wind_speed": current.get("wind_speed_10m"),
        "wind_display": format_speed(current.get("wind_speed_10m")),
        "wind_direction": current.get("wind_direction_10m"),
        "wind_gusts": current.get("wind_gusts_10m"),
        "precipitation": current.get("precipitation"),
        "rain_today": rain_today,
        "rain_display": format_millimeters(rain_today),
        "precipitation_probability": precipitation_probability,
        "pressure": current.get("pressure_msl"),
        "cloud_cover": current.get("cloud_cover"),
        "uv_index": uv_index,
        "evapotranspiration": evapotranspiration,
        "sunrise": format_time(get_daily_value(daily, "sunrise")),
        "sunset": format_time(get_daily_value(daily, "sunset")),
        "summary_cards": summary_cards,
        "forecast": build_daily_forecast(daily),
        "updated_at": current.get("time")
        or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "source": "Open-Meteo forecast API",
        "source_url": "https://open-meteo.com/",
        "errors": [],
    }


def find_hour_index(hours, current_time):
    if current_time in hours:
        return hours.index(current_time)
    if current_time:
        current_hour = current_time[:13]
        for index, hour in enumerate(hours):
            if hour[:13] == current_hour:
                return index
    return 0 if hours else None


def get_hourly_value(hourly, key, index):
    values = hourly.get(key, [])
    if index is None or index >= len(values):
        return None
    return values[index]


def get_daily_value(daily, key):
    values = daily.get(key, [])
    return values[0] if values else None


def format_celsius(value):
    if isinstance(value, (int, float)):
        return f"{round(value)} C"
    return "Weather unavailable"


def format_percent(value):
    if isinstance(value, (int, float)):
        return f"{round(value)}%"
    return "--"


def format_soil_moisture(value):
    if isinstance(value, (int, float)):
        return f"{round(value * 100)}%"
    return "--"


def format_speed(value):
    if isinstance(value, (int, float)):
        return f"{round(value)} km/h"
    return "-- km/h"


def format_degrees(value):
    if isinstance(value, (int, float)):
        return f"{round(value)} deg"
    return "--"


def format_millimeters(value):
    if isinstance(value, (int, float)):
        return f"{value:.1f} mm"
    return "-- mm"


def format_pressure(value):
    if isinstance(value, (int, float)):
        return f"{round(value)} hPa"
    return "--"


def format_number(value, digits=0):
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return "--"


def format_time(value):
    if isinstance(value, str) and "T" in value:
        return value.split("T", 1)[1]
    return value or "--"


def weather_code_label(code):
    labels = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Rime fog",
        51: "Light drizzle",
        53: "Drizzle",
        55: "Dense drizzle",
        61: "Light rain",
        63: "Rain",
        65: "Heavy rain",
        71: "Light snow",
        73: "Snow",
        75: "Heavy snow",
        80: "Light showers",
        81: "Showers",
        82: "Heavy showers",
        95: "Thunderstorm",
        96: "Thunderstorm hail",
        99: "Heavy hailstorm",
    }
    return labels.get(code, "Weather update")


def build_daily_forecast(daily):
    dates = daily.get("time", [])
    forecast = []
    for index, date in enumerate(dates[:3]):
        forecast.append(
            {
                "date": date,
                "condition": weather_code_label(get_list_value(daily, "weather_code", index)),
                "high": format_celsius(get_list_value(daily, "temperature_2m_max", index)),
                "low": format_celsius(get_list_value(daily, "temperature_2m_min", index)),
                "rain": format_millimeters(get_list_value(daily, "precipitation_sum", index)),
                "rain_chance": format_percent(
                    get_list_value(daily, "precipitation_probability_max", index)
                ),
                "wind": format_speed(get_list_value(daily, "wind_speed_10m_max", index)),
                "uv": format_number(get_list_value(daily, "uv_index_max", index), 1),
            }
        )
    return forecast


def build_weatherapi_forecast(forecast_days):
    forecast = []
    for item in forecast_days[:3]:
        day = item.get("day", {})
        forecast.append(
            {
                "date": item.get("date", "--"),
                "condition": (day.get("condition") or {}).get("text") or "Weather update",
                "high": format_celsius(day.get("maxtemp_c")),
                "low": format_celsius(day.get("mintemp_c")),
                "rain": format_millimeters(day.get("totalprecip_mm")),
                "rain_chance": format_percent(day.get("daily_chance_of_rain")),
                "wind": format_speed(day.get("maxwind_kph")),
                "uv": format_number(day.get("uv"), 1),
            }
        )
    return forecast


def get_list_value(data, key, index):
    values = data.get(key, [])
    if index >= len(values):
        return None
    return values[index]


class VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden_depth = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data):
        value = " ".join(data.split())
        if value and not self.hidden_depth:
            self.text.append(value)


def get_farmazone_market_data():
    now = time.time()
    if FARMAZONE_CACHE["data"] and FARMAZONE_CACHE["expires_at"] > now:
        return FARMAZONE_CACHE["data"]

    try:
        data = fetch_farmazone_market_data()
    except Exception as exc:
        data = dict(FARMAZONE_FALLBACK)
        data["success"] = False
        data["errors"] = [str(exc)]

    FARMAZONE_CACHE["data"] = data
    FARMAZONE_CACHE["expires_at"] = now + MARKET_CACHE_SECONDS
    return data


def fetch_farmazone_market_data():
    request = Request(FARMAZONE_URL, headers={"User-Agent": "SmartAgriculture/1.0"})
    with urlopen(request, timeout=8) as response:
        html = response.read().decode("utf-8", errors="replace")

    parser = VisibleTextParser()
    parser.feed(html)
    lines = parser.text

    indicators = extract_farmazone_indicators(lines)
    stats = extract_farmazone_stats(lines)

    if not indicators:
        raise ValueError("No Farmazone produce indicators found.")

    return {
        "success": True,
        "indicators": indicators,
        "stats": stats or FARMAZONE_FALLBACK["stats"],
        "updated_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "source": "Farmazone produce market intelligence",
        "source_url": FARMAZONE_URL,
        "errors": [],
    }


def extract_farmazone_indicators(lines):
    known_names = {
        "Potato (Class 1)",
        "Onions (Class 1)",
        "White Maize (SAFEX Spot)",
    }
    indicators = []

    for index, line in enumerate(lines):
        if line not in known_names:
            continue

        market = lines[index + 1] if index + 1 < len(lines) else ""
        price = lines[index + 2] if index + 2 < len(lines) else ""
        if market and price:
            indicators.append(
                {
                    "name": line,
                    "market": market,
                    "price_display": price,
                }
            )

    return indicators


def extract_farmazone_stats(lines):
    labels = {"Daily Log Updates", "Monitored Terminals", "Query Overhead"}
    stats = []

    for index, line in enumerate(lines):
        if line in labels and index > 0:
            stats.append({"value": lines[index - 1], "label": line})

    return stats


def get_market_prices():
    now = time.time()
    if MARKET_CACHE["data"] and MARKET_CACHE["expires_at"] > now:
        return MARKET_CACHE["data"]

    prices = []
    errors = []

    for market in MARKET_SYMBOLS:
        try:
            prices.append(fetch_market_price(market))
        except Exception as exc:
            errors.append(f"{market['symbol']}: {exc}")

    response = {
        "success": bool(prices),
        "prices": prices,
        "updated_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "source": "Yahoo Finance delayed futures data",
        "source_url": "https://finance.yahoo.com/commodities/",
        "errors": errors[:3],
    }
    MARKET_CACHE["data"] = response
    MARKET_CACHE["expires_at"] = now + MARKET_CACHE_SECONDS
    return response


def get_market_management_data():
    try:
        farmazone = get_farmazone_market_data()
        indicators = farmazone.get("indicators") or []
        if indicators:
            records = []
            for item in indicators:
                price_text = str(item.get("price_display") or "")
                match = re.search(r"R?([\d,]+\.?\d*)\s*[-–]\s*R?([\d,]+\.?\d*)", price_text)
                if match:
                    min_price = float(match.group(1).replace(",", ""))
                    max_price = float(match.group(2).replace(",", ""))
                    records.append({
                        "produce": item.get("name") or "Produce",
                        "type": "Vegetable",
                        "min": min_price,
                        "average": round((min_price + max_price) / 2, 2),
                        "max": max_price,
                        "unit": "per kg",
                        "market": item.get("market") or "Johburg Market",
                    })
            if records:
                return records
    except Exception:
        pass
    return [dict(item) for item in SOUTH_AFRICAN_MARKET_DATA]


def fetch_market_price(market):
    symbol = market["symbol"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?range=5d&interval=1d"
    request = Request(url, headers={"User-Agent": "SmartAgriculture/1.0"})

    with urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))

    result = payload["chart"]["result"][0]
    meta = result["meta"]
    closes = [
        value
        for value in result["indicators"]["quote"][0].get("close", [])
        if isinstance(value, (int, float))
    ]

    raw_price = meta.get("regularMarketPrice") or closes[-1]
    previous_close = meta.get("chartPreviousClose")
    if previous_close is None and len(closes) > 1:
        previous_close = closes[-2]

    scale = market["scale"]
    price = raw_price * scale
    previous_price = previous_close * scale if previous_close else None
    change_percent = None

    if previous_price:
        change_percent = ((price - previous_price) / previous_price) * 100

    return {
        "name": market["name"],
        "symbol": symbol,
        "price": round(price, 4),
        "price_display": format_market_price(price, market["unit"]),
        "unit": market["unit"],
        "change_percent": round(change_percent, 2) if change_percent is not None else None,
        "source": market["source"],
    }


def format_market_price(price, unit):
    if price >= 100:
        return f"${price:,.2f} {unit.replace('USD ', '')}"
    return f"${price:,.4f} {unit.replace('USD ', '')}"


def decode_harvest_code(code):
    try:
        parts = [int(part) for part in str(code).split("_")]
    except (TypeError, ValueError):
        return None
    if len(parts) != 4 or any(value < 1 or value > 36 for value in parts):
        return None
    return parts


def load_harvest_calendar(path):
    if not path.is_file():
        raise FileNotFoundError(f"Crop calendar CSV not found: {path}")

    source = pd.read_csv(path)
    required = [
        "uniqid_asap_0_1",
        "asap0_id",
        "name0_shr",
        "asap1_id",
        "name1_shr",
    ]
    missing = [column for column in required if column not in source.columns]
    if missing:
        raise ValueError(f"Crop calendar is missing required columns: {', '.join(missing)}")

    calendar = source.melt(
        id_vars=required,
        value_vars=[column for column in source.columns if column not in required],
        var_name="crop",
        value_name="calendar_code",
    )
    calendar = calendar.dropna(subset=["calendar_code"]).copy()
    calendar = calendar[
        calendar["name0_shr"].astype(str).str.strip().str.casefold() == "south africa"
    ].copy()
    calendar["calendar_code"] = calendar["calendar_code"].astype(str).str.strip()
    calendar["decoded"] = calendar["calendar_code"].map(decode_harvest_code)
    calendar = calendar.dropna(subset=["decoded"]).copy()

    if calendar.empty:
        raise ValueError("No valid South African crop-calendar records were found.")

    decoded_columns = [
        "planting_start",
        "planting_end",
        "harvest_start",
        "harvest_end",
    ]
    calendar[decoded_columns] = pd.DataFrame(calendar["decoded"].tolist(), index=calendar.index)
    for column in decoded_columns:
        calendar[column] = calendar[column].astype(int)
    return calendar


def load_harvest_model(path):
    if not path.is_file():
        return None
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            model = joblib.load(path)
        for warning in caught:
            logging.warning("Model loading warning: %s", warning.message)
        return model
    except Exception:
        logging.exception("Could not load Random Forest model from %s", path)
        return None


def date_to_dekad(value):
    period = 1 if value.day <= 10 else 2 if value.day <= 20 else 3
    return (value.month - 1) * 3 + period


def dekad_to_date(dekad, year):
    dekad = int(dekad)
    if not 1 <= dekad <= 36:
        raise ValueError("Dekad must be between 1 and 36.")
    month = (dekad - 1) // 3 + 1
    period = (dekad - 1) % 3
    day = 1 if period == 0 else 11 if period == 1 else 21
    return date(year, month, day)


def is_dekad_in_range(value, start, end):
    if start <= end:
        return start <= value <= end
    return value >= start or value <= end


def dekad_window(start, end, year):
    end_year = year + (1 if end < start else 0)
    return dekad_to_date(start, year), dekad_to_date(end, end_year)


def get_harvest_dates(planting_date, planting_dekad, harvest_start, harvest_end):
    start_year = planting_date.year + (1 if harvest_start < planting_dekad else 0)
    return dekad_window(harvest_start, harvest_end, start_year)


def format_harvest_date(value):
    return value.strftime("%d %B %Y")


def build_harvest_prediction(calendar, model, crop, region, planting_date_value):
    if isinstance(calendar, pd.DataFrame) and calendar.empty:
        return {"success": False, "error": "Harvest calendar data is not available yet."}

    if not crop or not region or not planting_date_value:
        return {"success": False, "error": "Crop, region, and planting date are required."}

    try:
        planting_date = datetime.strptime(str(planting_date_value), "%Y-%m-%d").date()
    except ValueError:
        return {"success": False, "error": "Planting date must use YYYY-MM-DD format."}

    match = calendar[(calendar["crop"] == crop) & (calendar["name1_shr"] == region)]
    if match.empty:
        return {"success": False, "error": "No crop-calendar record was found for that crop and region."}

    row = match.iloc[0]
    planting_dekad = date_to_dekad(planting_date)
    planting_start = int(row["planting_start"])
    planting_end = int(row["planting_end"])
    if not is_dekad_in_range(planting_dekad, planting_start, planting_end):
        window_start, window_end = dekad_window(planting_start, planting_end, planting_date.year)
        return {
            "success": False,
            "error": "Planting date is outside the regional planting period.",
            "planting_window": {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
                "start_label": format_harvest_date(window_start),
                "end_label": format_harvest_date(window_end),
            },
        }

    harvest_start, harvest_end = get_harvest_dates(
        planting_date,
        planting_dekad,
        int(row["harvest_start"]),
        int(row["harvest_end"]),
    )

    ml_result = None
    if model is not None:
        features = pd.DataFrame([{
            "crop": crop,
            "name1_shr": region,
            "planting_start": planting_start,
            "planting_end": planting_end,
        }])
        try:
            prediction = model.predict(features)[0]
            ml_start = int(np.clip(round(prediction[0]), 1, 36))
            ml_end = int(np.clip(round(prediction[1]), 1, 36))
            ml_year = planting_date.year + (1 if ml_start < planting_dekad else 0)
            ml_start_date, ml_end_date = dekad_window(ml_start, ml_end, ml_year)
            ml_result = {
                "start": ml_start_date.isoformat(),
                "end": ml_end_date.isoformat(),
                "start_label": format_harvest_date(ml_start_date),
                "end_label": format_harvest_date(ml_end_date),
            }
        except Exception:
            logging.exception("Random Forest prediction failed for %s / %s", crop, region)

    return {
        "success": True,
        "crop": crop,
        "region": region,
        "country": "South Africa",
        "planting_date": planting_date.isoformat(),
        "planting_date_label": format_harvest_date(planting_date),
        "harvest_window": {
            "start": harvest_start.isoformat(),
            "end": harvest_end.isoformat(),
            "start_label": format_harvest_date(harvest_start),
            "end_label": format_harvest_date(harvest_end),
        },
        "calendar_code": row["calendar_code"],
        "model_available": model is not None,
        "model_window": ml_result,
        "note": "This is a regional seasonal estimate, not a verified field-level maturity assessment.",
    }


def generate_harvest_analysis(payload):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {"success": False, "error": "AI analysis is not configured. Set GEMINI_API_KEY on the server to enable it."}

    required = ("crop", "region", "planting_date_label", "calendar_code", "harvest_window")
    if any(key not in payload for key in required):
        return {"success": False, "error": "Run a valid harvest prediction before requesting AI analysis."}

    window = payload["harvest_window"]
    if not isinstance(window, dict) or not all(key in window for key in ("start_label", "end_label")):
        return {"success": False, "error": "Prediction is missing its harvest window."}

    try:
        from google import genai

        prompt = f"""You are an agricultural analysis assistant helping farmers understand regional harvest estimates in South Africa.

CROP: {payload['crop']}
REGION: {payload['region']}, South Africa
PLANTING DATE: {payload['planting_date_label']}
REGIONAL CALENDAR CODE: {payload['calendar_code']}
EXPECTED HARVEST START: {window['start_label']}
EXPECTED HARVEST END: {window['end_label']}
MACHINE LEARNING RESULT: {payload.get('model_window') or 'No trained ML estimate is available.'}

Use these headings:
1. HARVEST WINDOW ANALYSIS
2. CROP MANAGEMENT
3. WEATHER CONSIDERATIONS
4. HARVEST PREPARATION
5. IMPORTANT LIMITATIONS

Give clear, practical advice for South African farmers. Do not claim the crop is ready,
invent live weather, crop observations, soil moisture, or yields. Distinguish regional
estimates from actual field maturity, and describe the Random Forest estimate as experimental."""
        with genai.Client(api_key=api_key) as client:
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        if not getattr(response, "text", None):
            return {"success": False, "error": "The AI service returned an empty response."}
        return {"success": True, "analysis": response.text}
    except Exception:
        logging.exception("Gemini analysis request failed")
        return {"success": False, "error": "AI analysis failed. Check the server logs and try again."}


def create_harvest_prediction_app(data_path=None, model_path=None):
    app = Flask(__name__)
    app.json.ensure_ascii = False

    load_dotenv(os.path.join(BASE_DIR, ".env"))
    harvest_data_file = Path(data_path) if data_path else Path(
        os.environ.get("HARVEST_DATA_PATH", os.path.join(BASE_DIR, "crop_calendar_gaul_asap1.csv"))
    )
    harvest_model_file = Path(model_path) if model_path else Path(
        os.environ.get("HARVEST_MODEL_PATH", os.path.join(BASE_DIR, "south_africa_harvest_window_model.pkl"))
    )

    calendar = load_harvest_calendar(harvest_data_file)
    model = load_harvest_model(harvest_model_file)
    app.config["CALENDAR"] = calendar
    app.config["MODEL"] = model

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "model_available": app.config["MODEL"] is not None})

    @app.get("/api/options")
    def options():
        frame = app.config["CALENDAR"]
        crops = sorted(frame["crop"].unique().tolist())
        crop = request.args.get("crop")
        regions = []
        if crop in crops:
            regions = sorted(
                frame.loc[frame["crop"] == crop, "name1_shr"].dropna().unique().tolist()
            )
        return jsonify({"crops": crops, "regions": regions})

    @app.post("/api/predict")
    def predict():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Send a JSON object with crop, region, and planting_date."}), 400

        crop = payload.get("crop")
        region = payload.get("region")
        raw_date = payload.get("planting_date")
        if not all(isinstance(value, str) and value.strip() for value in (crop, region, raw_date)):
            return jsonify({"error": "Crop, region, and planting date are required."}), 400
        try:
            planting_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            if planting_date.isoformat() != raw_date:
                raise ValueError("Date must use zero-padded ISO format.")
        except ValueError:
            return jsonify({"error": "Planting date must use YYYY-MM-DD format."}), 400

        frame = app.config["CALENDAR"]
        match = frame[(frame["crop"] == crop) & (frame["name1_shr"] == region)]
        if match.empty:
            return jsonify({"error": "No crop-calendar record was found for that crop and region."}), 404

        row = match.iloc[0]
        planting_dekad = date_to_dekad(planting_date)
        planting_start = int(row["planting_start"])
        planting_end = int(row["planting_end"])
        if not is_dekad_in_range(planting_dekad, planting_start, planting_end):
            window_start, window_end = dekad_window(planting_start, planting_end, planting_date.year)
            return jsonify({
                "error": "Planting date is outside the regional planting period.",
                "planting_window": {
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                    "start_label": format_harvest_date(window_start),
                    "end_label": format_harvest_date(window_end),
                },
            }), 422

        harvest_start, harvest_end = get_harvest_dates(
            planting_date,
            planting_dekad,
            int(row["harvest_start"]),
            int(row["harvest_end"]),
        )

        ml_result = None
        if app.config["MODEL"] is not None:
            features = pd.DataFrame([{
                "crop": crop,
                "name1_shr": region,
                "planting_start": planting_start,
                "planting_end": planting_end,
            }])
            try:
                prediction = app.config["MODEL"].predict(features)[0]
                ml_start = int(np.clip(round(prediction[0]), 1, 36))
                ml_end = int(np.clip(round(prediction[1]), 1, 36))
                ml_year = planting_date.year + (1 if ml_start < planting_dekad else 0)
                ml_start_date, ml_end_date = dekad_window(ml_start, ml_end, ml_year)
                ml_result = {
                    "start": ml_start_date.isoformat(),
                    "end": ml_end_date.isoformat(),
                    "start_label": format_harvest_date(ml_start_date),
                    "end_label": format_harvest_date(ml_end_date),
                }
            except Exception:
                logging.exception("Random Forest prediction failed for %s / %s", crop, region)

        return jsonify({
            "crop": crop,
            "region": region,
            "country": "South Africa",
            "planting_date": planting_date.isoformat(),
            "planting_date_label": format_harvest_date(planting_date),
            "harvest_window": {
                "start": harvest_start.isoformat(),
                "end": harvest_end.isoformat(),
                "start_label": format_harvest_date(harvest_start),
                "end_label": format_harvest_date(harvest_end),
            },
            "calendar_code": row["calendar_code"],
            "model_available": app.config["MODEL"] is not None,
            "model_window": ml_result,
            "note": "This is a regional seasonal estimate, not a verified field-level maturity assessment.",
        })

    @app.post("/api/analysis")
    def analysis():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Send a prediction result as a JSON object."}), 400
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            return jsonify({
                "error": "AI analysis is not configured. Set GEMINI_API_KEY on the server to enable it."
            }), 503

        required = ("crop", "region", "planting_date_label", "calendar_code", "harvest_window")
        if any(key not in payload for key in required):
            return jsonify({"error": "Run a valid harvest prediction before requesting AI analysis."}), 400
        window = payload["harvest_window"]
        if not isinstance(window, dict) or not all(key in window for key in ("start_label", "end_label")):
            return jsonify({"error": "Prediction is missing its harvest window."}), 400

        try:
            from google import genai

            prompt = f"""You are an agricultural analysis assistant helping farmers understand regional harvest estimates in South Africa.

CROP: {payload['crop']}
REGION: {payload['region']}, South Africa
PLANTING DATE: {payload['planting_date_label']}
REGIONAL CALENDAR CODE: {payload['calendar_code']}
EXPECTED HARVEST START: {window['start_label']}
EXPECTED HARVEST END: {window['end_label']}
MACHINE LEARNING RESULT: {payload.get('model_window') or 'No trained ML estimate is available.'}

Use these headings:
1. HARVEST WINDOW ANALYSIS
2. CROP MANAGEMENT
3. WEATHER CONSIDERATIONS
4. HARVEST PREPARATION
5. IMPORTANT LIMITATIONS

Give clear, practical advice for South African farmers. Do not claim the crop is ready,
invent live weather, crop observations, soil moisture, or yields. Distinguish regional
estimates from actual field maturity, and describe the Random Forest estimate as experimental."""
            with genai.Client(api_key=api_key) as client:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
            if not response.text:
                return jsonify({"error": "The AI service returned an empty response."}), 502
            return jsonify({"analysis": response.text})
        except Exception:
            logging.exception("Gemini analysis request failed")
            return jsonify({"error": "AI analysis failed. Check the server logs and try again."}), 502

    return app


def register_project_blueprints(app):
    try:
        from crop_disease_detection.routes import crop_disease_bp

        app.register_blueprint(crop_disease_bp)
    except Exception as exc:
        print(f"Warning: Crop Disease Detection project could not be registered: {exc}")

    try:
        from soil_classification.routes import soil_classification_bp

        app.register_blueprint(soil_classification_bp)
    except Exception as exc:
        print(f"Warning: Soil Classification project could not be registered: {exc}")

    try:
        from irrigation_prediction.routes import irrigation_prediction_bp

        app.register_blueprint(irrigation_prediction_bp)
    except Exception as exc:
        print(f"Warning: Irrigation Prediction project could not be registered: {exc}")


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = get_current_session_user()
        if not user:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = get_current_session_user()
        if not user:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        role = str(user.get("role") or session.get("role") or "user").strip().lower()
        if role != "admin":
            flash("Admin access is required.", "error")
            return redirect(url_for("user_dashboard"))
        return view(*args, **kwargs)

    return wrapped_view


def get_current_session_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    try:
        user = find_user_by_id(user_id)
    except Exception as exc:
        print(f"Warning: Session user could not be verified: {exc}")
        return None

    if not user:
        session.clear()
        flash("Your account was not found. Please sign in with a registered account.", "error")
        return None

    session["name"] = user["name"]
    session["role"] = str(user.get("role") or "user").strip().lower()
    return user


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, port=port)
