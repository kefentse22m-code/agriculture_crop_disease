"""Soil image classification using a YOLO classification model."""
import json
import os

from ultralytics import YOLO

from gemini_config import create_gemini_model


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(PROJECT_DIR, "assets", "best.pt")
GEMINI_MODEL_NAME = "gemini-2.5-flash"

SOIL_INFO = {
    "Clay": {
        "description": "Clay soil holds water and nutrients well but has poor drainage.",
        "good_crops": ["Rice", "Wheat", "Cabbage", "Broccoli"],
        "avoid": ["Crops that need loose soil, such as carrots"],
        "improvement": "Add compost and organic matter to improve drainage.",
        "fertilizer": "Use balanced fertilizer based on a soil test and avoid over-applying nitrogen.",
        "irrigation": "Water less often but monitor for waterlogging because clay drains slowly."
    },
    "Sandy": {
        "description": "Sandy soil drains quickly and has fewer nutrients.",
        "good_crops": ["Carrots", "Groundnuts", "Watermelon"],
        "avoid": ["Rice"],
        "improvement": "Add compost and mulch to retain moisture.",
        "fertilizer": "Apply smaller fertilizer doses more often to reduce nutrient leaching.",
        "irrigation": "Irrigate lightly and frequently because sandy soil dries quickly."
    },
    "Loamy": {
        "description": "Loamy soil is one of the best farming soils.",
        "good_crops": ["Maize", "Beans", "Tomatoes", "Vegetables"],
        "avoid": [],
        "improvement": "Maintain nutrients using compost.",
        "fertilizer": "Use crop-specific fertilizer rates and maintain organic matter.",
        "irrigation": "Keep moisture consistent and avoid both drought stress and saturation."
    },
    "Black": {
        "description": "Black soil is rich and holds moisture.",
        "good_crops": ["Cotton", "Wheat", "Sorghum"],
        "avoid": ["Excessive nitrogen fertilizer"],
        "improvement": "Monitor drainage and avoid too much nitrogen fertilizer.",
        "fertilizer": "Use phosphorus and potassium according to soil test results.",
        "irrigation": "Irrigate carefully because black soil can retain water for long periods."
    },
    "Peat": {
        "description": "Peat soil contains high organic matter.",
        "good_crops": ["Potatoes", "Blueberries"],
        "avoid": [],
        "improvement": "Monitor soil acidity.",
        "fertilizer": "Check pH and add lime or crop-specific nutrients only where needed.",
        "irrigation": "Keep evenly moist but make sure fields have controlled drainage."
    }
}

try:
    model = YOLO(MODEL_PATH)
    MODEL_LOADED = True
except Exception as exc:
    print(f"Warning: Could not load soil classification model: {exc}")
    model = None
    MODEL_LOADED = False


def classify_soil(image):
    """Classify soil from a PIL image."""
    if not MODEL_LOADED or model is None:
        raise RuntimeError("Soil classification model is not available.")

    results = model.predict(image, verbose=False)
    probabilities = results[0].probs

    if probabilities is None:
        raise RuntimeError("Model did not return classification probabilities.")

    class_id = int(probabilities.data.argmax())
    confidence = float(probabilities.data[class_id])
    soil_type = model.names[class_id]
    confidence_percent = round(confidence * 100, 2)
    info = generate_soil_report(soil_type, confidence_percent)

    return {
        "soil_type": soil_type,
        "confidence": confidence_percent,
        "info": info
    }


def _as_text(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    if value is None:
        return ""
    return str(value)


def _fallback_report(soil_type):
    info = SOIL_INFO.get(soil_type, {})
    return {
        "description": _as_text(info.get("description")) or "Information unavailable.",
        "good_crops": _as_text(info.get("good_crops")) or "Information unavailable.",
        "avoid": _as_text(info.get("avoid")) or "Information unavailable.",
        "improvement": _as_text(info.get("improvement")) or "Information unavailable.",
        "fertilizer": _as_text(info.get("fertilizer")) or "Information unavailable.",
        "irrigation": _as_text(info.get("irrigation")) or "Information unavailable.",
    }


def _parse_report(text, soil_type):
    cleaned = text.strip().replace("```json", "").replace("```", "").strip()
    report = json.loads(cleaned)
    fallback = _fallback_report(soil_type)

    return {
        "description": _as_text(report.get("description")) or fallback["description"],
        "good_crops": _as_text(report.get("good_crops")) or fallback["good_crops"],
        "avoid": _as_text(report.get("avoid")) or fallback["avoid"],
        "improvement": _as_text(report.get("improvement")) or fallback["improvement"],
        "fertilizer": _as_text(report.get("fertilizer")) or fallback["fertilizer"],
        "irrigation": _as_text(report.get("irrigation")) or fallback["irrigation"],
    }


def generate_soil_report(soil_type, confidence):
    """Generate the same practical JSON soil report used by the Gradio version."""
    gemini_model = create_gemini_model(GEMINI_MODEL_NAME)
    if gemini_model is None:
        return _fallback_report(soil_type)

    prompt = f"""
You are an expert agricultural soil scientist.

The soil classifier identified:

Soil Type: {soil_type}
Confidence: {confidence:.2f}%

Return ONLY valid JSON.

{{
    "description":"",
    "good_crops":"",
    "avoid":"",
    "improvement":"",
    "fertilizer":"",
    "irrigation":""
}}

Rules:
- Keep recommendations practical.
- Focus on agriculture.
- No markdown.
- No explanations outside JSON.
"""

    try:
        response = gemini_model.generate_content(prompt)
        return _parse_report(response.text, soil_type)
    except Exception as exc:
        print(f"Warning: Gemini soil report failed: {exc}")
        return _fallback_report(soil_type)
