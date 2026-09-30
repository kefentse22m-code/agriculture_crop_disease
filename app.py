from functools import wraps
import json
import os
import time
from html.parser import HTMLParser
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

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
    create_user,
    find_user_by_email,
    find_user_by_id,
    get_all_users,
    get_user_history,
    init_db,
    record_auth_event,
)


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
APPLICATION_PREFIX = "/cropdetaction"
MARKET_CACHE_SECONDS = 15 * 60
MARKET_CACHE = {"expires_at": 0, "data": None}
WEATHER_CACHE_SECONDS = 10 * 60
WEATHER_CACHE = {}
DEFAULT_FIELD_NAME = os.environ.get("DEFAULT_FIELD_NAME", "Field A")
DEFAULT_FIELD_LATITUDE = float(os.environ.get("DEFAULT_FIELD_LATITUDE", "-25.7479"))
DEFAULT_FIELD_LONGITUDE = float(os.environ.get("DEFAULT_FIELD_LONGITUDE", "28.2293"))
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "").strip()
FARMAZONE_URL = "https://farmazone.co.za/produce/"
FARMAZONE_CACHE = {"expires_at": 0, "data": None}

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
        return jsonify(get_farmazone_market_data())

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
                flash("Account created. You can now log in.", "success")
                return redirect(url_for("login"))
            except DuplicateUserError:
                save_auth_event(None, email, "register", False, {"reason": "duplicate_email"})
                flash("An account with that email already exists.", "error")

        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            user = find_user_by_email(email)
            if not user or not check_password_hash(user["password_hash"], password):
                save_auth_event(
                    user.get("id") if user else None,
                    email,
                    "login",
                    False,
                    {"reason": "invalid_credentials"},
                )
                flash("Invalid email or password.", "error")
                return render_template("login.html")

            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            save_auth_event(user["id"], email, "login", True, {"role": user["role"]})

            if user["role"] == "admin":
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

    @app.route("/dashboard/irrigation-prediction")
    @login_required
    def irrigation_prediction_page():
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return render_template("irrigation_prediction.html")

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

    @app.route("/api/history")
    @login_required
    def history():
        return jsonify({"success": True, "history": safe_get_user_history(session["user_id"])})

    @app.route("/logout")
    def logout():
        save_auth_event(
            session.get("user_id"),
            None,
            "logout",
            True,
            {"role": session.get("role")},
        )
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("landing"))

    return app


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
            "updated_at": "WeatherAPI unavailable",
            "source": "WeatherAPI unavailable",
            "source_url": "https://www.weatherapi.com/",
            "errors": [str(exc)],
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
        raise RuntimeError("WEATHER_API_KEY is not configured.")

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
        "forecast_days": 3,
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
        if user["role"] != "admin":
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
    session["role"] = user["role"]
    return user


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, port=port)
