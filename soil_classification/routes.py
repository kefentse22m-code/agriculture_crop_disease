"""Soil Classification project routes."""
import io
import json
import os
import base64

from flask import Blueprint, jsonify, request, session
from PIL import Image

from database import record_project_history
from gemini_config import assistant_available
from .scripts.chat import ask_soil_assistant
from .scripts.classifier import MODEL_LOADED, SOIL_INFO, classify_soil


PROJECT_DIR = os.path.dirname(__file__)

soil_classification_bp = Blueprint(
    "soil_classification",
    __name__,
    url_prefix="/api/soil_classification"
)


def load_config():
    config_file = os.path.join(PROJECT_DIR, "config.json")
    with open(config_file, "r") as file:
        return json.load(file)


def save_history(*args, **kwargs):
    try:
        return record_project_history(*args, **kwargs)
    except Exception as exc:
        print(f"Warning: Soil classification history could not be saved: {exc}")
        return None


def encode_preview(image):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@soil_classification_bp.route("/config", methods=["GET"])
def get_config():
    config = load_config()
    config["model_available"] = MODEL_LOADED
    config["ai_assistant_available"] = assistant_available()
    return jsonify({"success": True, "data": config}), 200


@soil_classification_bp.route("/classify", methods=["POST"])
def classify():
    try:
        if not MODEL_LOADED:
            return jsonify({
                "success": False,
                "error": "Soil classification model is not available."
            }), 503

        if "image" not in request.files:
            return jsonify({"success": False, "error": "No image provided"}), 400

        file = request.files["image"]
        if file.filename == "":
            return jsonify({"success": False, "error": "No selected file"}), 400

        file_bytes = file.read()
        image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        result = classify_soil(image)
        result["image"] = encode_preview(image)
        response_payload = {"success": True, "data": result}

        save_history(
            user_id=session.get("user_id"),
            project="soil_classification",
            action="upload",
            request_payload={
                "filename": file.filename,
                "content_type": file.content_type,
                "image_bytes": len(file_bytes),
            },
            response_payload=response_payload,
            upload_filename=file.filename,
            upload_content_type=file.content_type,
            file_bytes=file_bytes,
        )

        return jsonify(response_payload), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@soil_classification_bp.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json() or {}
        soil_type = data.get("soil_type")
        confidence = data.get("confidence")
        soil_context = data.get("soil_context") or data.get("info")
        message = data.get("message", "")

        if not soil_type:
            return jsonify({"success": False, "error": "soil_type is required"}), 400
        if not message:
            return jsonify({"success": False, "error": "message is required"}), 400

        response = ask_soil_assistant(
            message,
            soil_type,
            confidence=confidence,
            soil_context=soil_context,
        )
        response_payload = {"success": True, "response": response}
        save_history(
            user_id=session.get("user_id"),
            project="soil_classification",
            action="chat",
            request_payload={
                "soil_type": soil_type,
                "confidence": confidence,
                "soil_context": soil_context,
                "message": message,
            },
            response_payload=response_payload,
        )
        return jsonify(response_payload), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@soil_classification_bp.route("/soil-info/<soil_type>", methods=["GET"])
def soil_info(soil_type):
    info = SOIL_INFO.get(soil_type)
    if info is None:
        return jsonify({"success": False, "error": "Unknown soil type"}), 404

    return jsonify({"success": True, "soil_type": soil_type, "info": info}), 200
