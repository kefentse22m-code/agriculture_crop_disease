"""Irrigation need prediction based on the saved notebook pipeline."""
import os
from urllib.parse import urlencode

import joblib
import pandas as pd
import requests
from dotenv import load_dotenv


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODEL_PATH = os.path.join(BASE_DIR, "models", "irrigation_model.pkl")
load_dotenv(os.path.join(BASE_DIR, ".env"))
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "").strip()

FEATURES = [
    "Soil_Type",
    "Soil_Moisture",
    "Temperature_C",
    "Humidity",
    "Rainfall_mm",
    "Wind_Speed_kmh",
    "Crop_Type",
    "Crop_Growth_Stage",
    "Season",
    "Field_Area_hectare",
    "Mulching_Used",
]

CROP_TYPES = ["Cotton", "Maize", "Potato", "Rice", "Sugarcane", "Wheat"]
SOIL_TYPES = ["Clay", "Loamy", "Sandy", "Silt"]
GROWTH_STAGES = ["Flowering", "Harvest", "Sowing", "Vegetative"]
SEASONS = ["Kharif", "Rabi", "Zaid"]
MULCHING_OPTIONS = ["Yes", "No"]

try:
    model = joblib.load(MODEL_PATH)
    MODEL_LOADED = True
except Exception as exc:
    print(f"Warning: Could not load irrigation prediction model: {exc}")
    model = None
    MODEL_LOADED = False


def predict_irrigation(payload):
    """Predict irrigation need from form data and live weather."""
    if not MODEL_LOADED or model is None:
        raise RuntimeError("Irrigation prediction model is not available.")

    latitude = _to_float(payload.get("latitude"), "latitude")
    longitude = _to_float(payload.get("longitude"), "longitude")
    crop_type = _choice(payload.get("crop_type"), CROP_TYPES, "crop_type")
    soil_type = _choice(payload.get("soil_type"), SOIL_TYPES, "soil_type")
    soil_moisture = _to_float(payload.get("soil_moisture"), "soil_moisture")
    field_area = _to_float(payload.get("field_area"), "field_area")
    mulching_used = _choice(payload.get("mulching_used") or "No", MULCHING_OPTIONS, "mulching_used")
    growth_stage = _choice(
        payload.get("growth_stage") or "Vegetative",
        GROWTH_STAGES,
        "growth_stage",
    )
    season = _choice(payload.get("season") or "Kharif", SEASONS, "season")
    planting_date = (payload.get("planting_date") or "").strip()

    weather = get_weather(latitude, longitude)
    input_df = pd.DataFrame([{
        "Soil_Type": soil_type,
        "Soil_Moisture": soil_moisture,
        "Temperature_C": weather["Temperature_C"],
        "Humidity": weather["Humidity"],
        "Rainfall_mm": weather["Rainfall_mm"],
        "Wind_Speed_kmh": weather["Wind_Speed_kmh"],
        "Crop_Type": crop_type,
        "Crop_Growth_Stage": growth_stage,
        "Season": season,
        "Field_Area_hectare": field_area,
        "Mulching_Used": mulching_used,
    }], columns=FEATURES)

    prediction = str(model.predict(input_df)[0])
    probabilities = _prediction_probabilities(input_df)
    explanation = build_explanation(
        prediction,
        soil_moisture,
        weather,
        growth_stage,
        mulching_used,
    )

    return {
        "prediction": prediction,
        "risk_level": risk_level(prediction),
        "confidence": prediction_confidence(probabilities, prediction),
        "explanation": explanation,
        "recommendation": build_recommendation(
            prediction,
            field_area,
            mulching_used,
            soil_moisture,
            weather,
        ),
        "weather": weather,
        "inputs": {
            "latitude": latitude,
            "longitude": longitude,
            "crop_type": crop_type,
            "soil_type": soil_type,
            "planting_date": planting_date,
            "soil_moisture": soil_moisture,
            "field_area": field_area,
            "mulching_used": mulching_used,
            "growth_stage": growth_stage,
            "season": season,
        },
        "probabilities": probabilities,
    }


def risk_level(prediction):
    return {
        "High": "High",
        "Medium": "Moderate",
        "Low": "Low",
    }.get(prediction, "Unknown")


def prediction_confidence(probabilities, prediction):
    for item in probabilities:
        if item["label"] == prediction:
            return item["probability"]
    return None


def build_explanation(prediction, soil_moisture, weather, growth_stage, mulching_used):
    factors = []
    if soil_moisture < 30:
        factors.append("soil moisture is low")
    elif soil_moisture > 70:
        factors.append("soil moisture is already high")

    if weather["Rainfall_mm"] >= 5:
        factors.append("recent rainfall may reduce immediate water demand")
    elif weather["Rainfall_mm"] == 0:
        factors.append("no current rainfall was recorded")

    if weather["Temperature_C"] >= 30:
        factors.append("high temperature may increase crop water loss")
    if weather["Wind_Speed_kmh"] >= 25:
        factors.append("strong wind may increase evaporation")
    if growth_stage == "Flowering":
        factors.append("the flowering stage requires consistent moisture")
    if mulching_used == "Yes":
        factors.append("mulching can reduce surface water loss")

    if not factors:
        factors.append("the available crop, soil and weather inputs indicate normal demand")

    return {
        "summary": f"{risk_level(prediction)} irrigation demand was predicted from the submitted field and weather data.",
        "factors": factors[:5],
        "data_sources": ["User field inputs", "Weather service", "Irrigation prediction model"],
    }


def get_weather(latitude, longitude):
    """Fetch current weather using the configured WeatherAPI key."""
    if not WEATHER_API_KEY:
        raise RuntimeError("WEATHER_API_KEY is not configured.")

    return get_weatherapi_weather(latitude, longitude)


def get_weatherapi_weather(latitude, longitude):
    url = "https://api.weatherapi.com/v1/current.json"
    response = requests.get(
        url,
        params={"key": WEATHER_API_KEY, "q": f"{latitude},{longitude}"},
        timeout=10,
    )
    response.raise_for_status()
    current = response.json()["current"]
    return {
        "Temperature_C": float(current["temp_c"]),
        "Humidity": float(current["humidity"]),
        "Rainfall_mm": float(current.get("precip_mm", 0) or 0),
        "Wind_Speed_kmh": float(current["wind_kph"]),
        "source": "WeatherAPI current weather",
    }


def get_open_meteo_weather(latitude, longitude):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
        "timezone": "Africa/Johannesburg",
    }
    url = f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    current = response.json().get("current", {})
    return {
        "Temperature_C": float(current.get("temperature_2m") or 0),
        "Humidity": float(current.get("relative_humidity_2m") or 0),
        "Rainfall_mm": float(current.get("precipitation") or 0),
        "Wind_Speed_kmh": float(current.get("wind_speed_10m") or 0),
        "source": "Open-Meteo current weather",
    }


def build_recommendation(prediction, field_area, mulching_used, soil_moisture, weather):
    action = {
        "High": "Irrigate soon and monitor the field closely.",
        "Medium": "Apply moderate irrigation and reassess moisture later today.",
        "Low": "Delay irrigation unless crop stress appears.",
    }.get(prediction, "Apply irrigation according to the predicted requirement.")

    return (
        f"Predicted Irrigation Need: {prediction}\n\n"
        f"Field Area: {field_area:g} hectares\n"
        f"Mulching Used: {mulching_used}\n"
        f"Soil Moisture: {soil_moisture:g}%\n"
        f"Rainfall Now: {weather['Rainfall_mm']:g} mm\n\n"
        f"Recommended Action:\n{action}"
    )


def _prediction_probabilities(input_df):
    if not hasattr(model, "predict_proba"):
        return []

    try:
        classes = list(model.classes_)
        values = model.predict_proba(input_df)[0]
        return [
            {"label": str(label), "probability": round(float(probability) * 100, 2)}
            for label, probability in zip(classes, values)
        ]
    except Exception:
        return []


def _to_float(value, field_name):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a number.")


def _choice(value, choices, field_name):
    if value not in choices:
        raise ValueError(f"{field_name} must be one of: {', '.join(choices)}.")
    return value
