"""Shared Gemini configuration for all Smart Agriculture projects."""
import os

from dotenv import load_dotenv
import google.generativeai as genai


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def get_gemini_api_key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def assistant_available():
    return bool(get_gemini_api_key())


def configure_gemini():
    api_key = get_gemini_api_key()
    if not api_key:
        return False

    genai.configure(api_key=api_key)
    return True


def create_gemini_model(model_name="gemini-2.0-flash"):
    if not configure_gemini():
        return None

    return genai.GenerativeModel(model_name)
