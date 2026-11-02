"""Irrigation Prediction project routes."""
import json
import os

from flask import Blueprint, jsonify, request, session

from database import record_project_history
from gemini_config import assistant_available
from .scripts.predict import (
    CROP_TYPES,
    GROWTH_STAGES,
    MODEL_LOADED,
    MULCHING_OPTIONS,
    SEASONS,
    SOIL_TYPES,
    predict_irrigation,
)
from .scripts.report import generate_irrigation_report


PROJECT_DIR = os.path.dirname(__file__)

irrigation_prediction_bp = Blueprint(
    "irrigation_prediction",
    __name__,
    url_prefix="/api/irrigation_prediction",
)


def load_config():
    config_file = os.path.join(PROJECT_DIR, "config.json")
    with open(config_file, "r") as file:
        return json.load(file)


def save_history(*args, **kwargs):
    try:
        return record_project_history(*args, **kwargs)
    except Exception as exc:
        print(f"Warning: Irrigation prediction history could not be saved: {exc}")
        return None


@irrigation_prediction_bp.route("/config", methods=["GET"])
def get_config():
    config = load_config()
    config.update({
        "model_available": MODEL_LOADED,
        "ai_assistant_available": assistant_available(),
        "crop_types": CROP_TYPES,
        "soil_types": SOIL_TYPES,
        "growth_stages": GROWTH_STAGES,
        "seasons": SEASONS,
        "mulching_options": MULCHING_OPTIONS,
    })
    return jsonify({"success": True, "data": config}), 200


@irrigation_prediction_bp.route("/predict", methods=["POST"])
def predict():
    try:
        if not MODEL_LOADED:
            return jsonify({
                "success": False,
                "error": "Irrigation prediction model is not available.",
            }), 503

        payload = request.get_json() or {}
        result = predict_irrigation(payload)
        result["report"] = generate_irrigation_report(result)
        response_payload = {"success": True, "data": result}

        save_history(
            user_id=session.get("user_id"),
            project="irrigation_prediction",
            action="predict",
            request_payload=payload,
            response_payload=response_payload,
        )

        return jsonify(response_payload), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
