"""Gemini report generation for irrigation predictions."""
from gemini_config import create_gemini_model


GEMINI_MODEL_NAME = "gemini-2.5-flash"


def generate_irrigation_report(result):
    """Generate the detailed report used by the notebook's Gradio app."""
    gemini_model = create_gemini_model(GEMINI_MODEL_NAME)
    if gemini_model is None:
        return fallback_report(result)

    inputs = result["inputs"]
    weather = result["weather"]
    prediction = result["prediction"]

    prompt = f"""
You are an agricultural irrigation expert.

Crop Type:
{inputs['crop_type']}

Soil Type:
{inputs['soil_type']}

Growth Stage:
{inputs['growth_stage']}

Weather Forecast:
Temperature: {weather['Temperature_C']} C
Humidity: {weather['Humidity']}
Rainfall: {weather['Rainfall_mm']} mm
Wind Speed: {weather['Wind_Speed_kmh']} km/h

Predicted Irrigation Need:
{prediction}

Generate:

1. Irrigation recommendation
2. Water management advice
3. Risk assessment
4. Crop health guidance
5. Detailed irrigation report
"""

    try:
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as exc:
        print(f"Warning: Gemini irrigation report failed: {exc}")
        return fallback_report(result)


def fallback_report(result):
    inputs = result["inputs"]
    weather = result["weather"]
    prediction = result["prediction"]

    return (
        f"Irrigation recommendation: {result['recommendation']}\n\n"
        f"Water management advice: Use the {prediction.lower()} irrigation need together "
        f"with local field observations before applying water.\n\n"
        f"Risk assessment: Temperature is {weather['Temperature_C']:g} C, humidity is "
        f"{weather['Humidity']:g}%, rainfall is {weather['Rainfall_mm']:g} mm, and wind "
        f"speed is {weather['Wind_Speed_kmh']:g} km/h.\n\n"
        f"Crop health guidance: Check {inputs['crop_type']} plants in the "
        f"{inputs['growth_stage'].lower()} stage for wilting, leaf rolling, or saturated "
        f"soil before irrigation."
    )
