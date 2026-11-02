"""Gemini-backed farming advice for soil classification results."""

from gemini_config import create_gemini_model
from .classifier import SOIL_INFO

GEMINI_MODEL_NAME = "gemini-2.5-flash"


def _as_text(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    if value is None:
        return ""
    return str(value)


def _fallback_info(soil_type):
    return SOIL_INFO.get(soil_type, {})


def _format_soil_details(soil_type, soil_context=None):
    info = soil_context or _fallback_info(soil_type)
    return (
        f"Description: {_as_text(info.get('description')) or 'No extra information available.'}\n"
        f"Suitable Crops: {_as_text(info.get('good_crops')) or 'Not specified'}\n"
        f"Avoid: {_as_text(info.get('avoid')) or 'Not specified'}\n"
        f"Improvement: {_as_text(info.get('improvement')) or 'No improvement advice available.'}\n"
        f"Fertilizer: {_as_text(info.get('fertilizer')) or 'No fertilizer advice available.'}\n"
        f"Irrigation: {_as_text(info.get('irrigation')) or 'No irrigation advice available.'}"
    )


def ask_soil_assistant(question, soil_type, confidence=None, soil_context=None):
    """Answer a farming question using the classified soil type as context."""
    model = create_gemini_model(GEMINI_MODEL_NAME)
    if model is None:
        return "Gemini API key is not configured. Add GEMINI_API_KEY to .env to enable AI advice."

    confidence_line = (
        f"{float(confidence):.2f}%"
        if confidence is not None
        else "Not supplied"
    )

    prompt = f"""
You are an agricultural consultant.

Current Soil Analysis

Soil Type:
{soil_type}

Confidence:
{confidence_line}

{_format_soil_details(soil_type, soil_context)}

Answer the farmer's question clearly and practically.

Question:
{question}
"""

    response = model.generate_content(prompt)
    return response.text
