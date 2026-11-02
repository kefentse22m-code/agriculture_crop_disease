import base64
import io
import os
from functools import lru_cache

import torch
import torch.nn as nn
from flask import Blueprint, jsonify, request, session
from PIL import Image
from torchvision import models, transforms

from database import record_project_history
from gemini_config import assistant_available, create_gemini_model


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODEL_PATH = os.path.join(BASE_DIR, "models", "plant_disease_model.pth")

CLASS_NAMES = [
    "Apple___Apple_scab",
    "Apple___Black_rot",
    "Apple___Cedar_apple_rust",
    "Apple___healthy",
    "Blueberry___healthy",
    "Cherry_(including_sour)___Powdery_mildew",
    "Cherry_(including_sour)___healthy",
    "Corn_(maize)___Cercospora_leaf_spot Gray_leaf_spot",
    "Corn_(maize)___Common_rust_",
    "Corn_(maize)___Northern_Leaf_Blight",
    "Corn_(maize)___healthy",
    "Grape___Black_rot",
    "Grape___Esca_(Black_Measles)",
    "Grape___Leaf_blight_(Isariopsis_Leaf_Spot)",
    "Grape___healthy",
    "Orange___Haunglongbing_(Citrus_greening)",
    "Peach___Bacterial_spot",
    "Peach___healthy",
    "Pepper,_bell___Bacterial_spot",
    "Pepper,_bell___healthy",
    "Potato___Early_blight",
    "Potato___Late_blight",
    "Potato___healthy",
    "Raspberry___healthy",
    "Soybean___healthy",
    "Squash___Powdery_mildew",
    "Strawberry___Leaf_scorch",
    "Strawberry___healthy",
    "Tomato___Bacterial_spot",
    "Tomato___Early_blight",
    "Tomato___Late_blight",
    "Tomato___Leaf_Mold",
    "Tomato___Septoria_leaf_spot",
    "Tomato___Spider_mites Two-spotted_spider_mite",
    "Tomato___Target_Spot",
    "Tomato___Tomato_Yellow_Leaf_Curl_Virus",
    "Tomato___Tomato_mosaic_virus",
    "Tomato___healthy",
]

IMAGE_TRANSFORM = transforms.Compose(
    [
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
    ]
)

crop_disease_bp = Blueprint(
    "crop_disease_detection",
    __name__,
    url_prefix="/api/crop_disease_detection",
)


def readable_label(label):
    plant, disease = label.split("___", 1)
    plant = plant.replace("_", " ")
    disease = disease.replace("_", " ").replace("  ", " ")
    return f"{plant}: {disease}"


def get_recommendation(label):
    disease = label.lower()

    if "healthy" in disease:
        return "Plant appears healthy. Keep monitoring leaves and maintain good watering, sunlight, and airflow."
    if "blight" in disease:
        return "Remove infected leaves, avoid overhead watering, and apply an appropriate fungicide if symptoms spread."
    if "rust" in disease:
        return "Remove affected leaves and apply a rust treatment or fungicide recommended for this crop."
    if "bacterial" in disease:
        return "Remove infected material, avoid splashing water, sanitize tools, and consider a copper-based bactericide."
    if "mildew" in disease:
        return "Improve airflow, avoid wet leaves overnight, and use a suitable mildew fungicide when needed."
    if "spot" in disease or "scab" in disease:
        return "Remove badly affected leaves, keep foliage dry, and apply a crop-safe treatment for leaf spot diseases."
    if "virus" in disease or "mosaic" in disease or "curl" in disease:
        return "Remove infected plants where practical and control insect vectors such as whiteflies or aphids."

    return "Consult a local agricultural expert for crop-specific treatment guidance."


@lru_cache(maxsize=1)
def load_model():
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = models.efficientnet_b0(weights=None)
    num_features = model.classifier[1].in_features
    model.classifier[1] = nn.Linear(num_features, len(CLASS_NAMES))

    state_dict = torch.load(MODEL_PATH, map_location=device)
    if any(key.startswith("module.") for key in state_dict):
        state_dict = {key.removeprefix("module."): value for key, value in state_dict.items()}

    model.load_state_dict(state_dict)
    model.to(device)
    model.eval()
    return model, device


def predict_disease(image):
    model, device = load_model()
    tensor = IMAGE_TRANSFORM(image).unsqueeze(0).to(device)

    with torch.no_grad():
        outputs = model(tensor)
        probabilities = torch.softmax(outputs, dim=1)
        confidence, predicted = torch.max(probabilities, 1)

    raw_label = CLASS_NAMES[predicted.item()]
    return {
        "raw_label": raw_label,
        "label": readable_label(raw_label),
        "confidence": float(confidence.item()),
        "recommendation": get_recommendation(raw_label),
    }


def encode_preview(image):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def save_history(*args, **kwargs):
    try:
        return record_project_history(*args, **kwargs)
    except Exception as exc:
        print(f"Warning: Crop disease history could not be saved: {exc}")
        return None


@crop_disease_bp.route("/info")
def info():
    model_available = os.path.exists(MODEL_PATH)
    return jsonify(
        {
            "model_available": model_available,
            "ai_assistant_available": assistant_available(),
            "classes": len(CLASS_NAMES),
        }
    )


@crop_disease_bp.route("/upload", methods=["POST"])
def upload():
    uploaded_file = request.files.get("file")
    if uploaded_file is None or uploaded_file.filename == "":
        return jsonify({"error": "Please upload a crop leaf image."}), 400

    file_bytes = uploaded_file.read()
    try:
        image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    except Exception:
        return jsonify({"error": "The uploaded file is not a valid image."}), 400

    try:
        prediction = predict_disease(image)
    except Exception as exc:
        return jsonify({"error": f"Could not run the crop disease model: {exc}"}), 500

    confidence_percent = prediction["confidence"] * 100
    analysis = (
        f"{prediction['label']} detected with {confidence_percent:.2f}% confidence. "
        f"{prediction['recommendation']}"
    )
    response_payload = {
        "image": encode_preview(image),
        "label": prediction["label"],
        "analysis": analysis,
        "confidence": prediction["confidence"],
        "detected_diseases": [
            prediction["label"],
            f"{confidence_percent:.2f}% confidence",
        ],
        "recommendation": prediction["recommendation"],
    }

    save_history(
        user_id=session.get("user_id"),
        project="crop_disease_detection",
        action="upload",
        request_payload={"filename": uploaded_file.filename},
        response_payload=response_payload,
        upload_filename=uploaded_file.filename,
        upload_content_type=uploaded_file.content_type,
        file_bytes=file_bytes,
    )

    return jsonify(response_payload)


@crop_disease_bp.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    chat_history = (data.get("chatHistory") or "").strip()
    latest_result = data.get("latestResult") or {}

    if not message:
        return jsonify({"error": "Please enter a question."}), 400

    if not assistant_available():
        response_payload = {
            "response": (
                "The AI assistant is offline because GEMINI_API_KEY is not configured. "
                "You can still use the disease classifier and review its recommendation."
            )
        }
        save_history(
            user_id=session.get("user_id"),
            project="crop_disease_detection",
            action="chat",
            request_payload={
                "message": message,
                "latest_result": latest_result,
            },
            response_payload=response_payload,
        )
        return jsonify(response_payload)

    try:
        model = create_gemini_model("gemini-2.5-flash")
        result_context = build_result_context(latest_result)
        prompt = (
            "You are a practical crop disease assistant. Give concise, safe farming advice. "
            "Use the current classifier result as context when it is provided. "
            "Do not ask the farmer what the problem is if the classifier result already identifies it. "
            "Do not claim certainty beyond the image classifier result.\n\n"
            f"{result_context}\n\nConversation so far:\n{chat_history}\n\nFarmer question: {message}"
        )
        response = model.generate_content(prompt)
        response_payload = {"response": response.text}
        save_history(
            user_id=session.get("user_id"),
            project="crop_disease_detection",
            action="chat",
            request_payload={
                "message": message,
                "chat_history": chat_history,
                "latest_result": latest_result,
            },
            response_payload=response_payload,
        )
        return jsonify(response_payload)
    except Exception as exc:
        return jsonify({"error": f"Assistant unavailable: {exc}"}), 500


def build_result_context(latest_result):
    if not isinstance(latest_result, dict) or not latest_result.get("label"):
        return (
            "Current classifier result: none provided yet. "
            "If the farmer asks for treatment, ask them to upload a crop leaf image first."
        )

    confidence = latest_result.get("confidence")
    if isinstance(confidence, (int, float)):
        confidence_text = f"{confidence * 100:.2f}%"
    else:
        confidence_text = "not provided"

    recommendation = latest_result.get("recommendation") or "No recommendation provided."
    analysis = latest_result.get("analysis") or ""

    return (
        "Current classifier result:\n"
        f"- Predicted problem: {latest_result['label']}\n"
        f"- Confidence: {confidence_text}\n"
        f"- App recommendation: {recommendation}\n"
        f"- Analysis summary: {analysis}"
    )
