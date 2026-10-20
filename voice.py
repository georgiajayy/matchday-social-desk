"""
voice.py
The AI commentator's voice.

Two engines, tried in this order:
  1. gpt-audio-mini (Microsoft Foundry): a natural, directable voice. We tell it HOW to
     speak (a natural, relaxed British commentator at a brisk pace) and it reads the caption.
     Set AZURE_AUDIO_DEPLOYMENT=gpt-audio-mini in .env to switch it on.
  2. Azure AI Speech (neural text to speech): the reliable fallback.

Test:  python voice.py
"""

import base64
import hashlib
import os
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from dotenv import load_dotenv

load_dotenv()

AUDIO_DIR = Path(__file__).parent / "output" / "audio"

# ---- engine 1: gpt-audio-mini, directed like a voice actor ----
# One natural voice for every pundit: the words carry the London/Northern/Midlands/Posh character,
# the voice stays relaxed and human (AI "accent impressions" sound fake).
NATURAL_DIRECTION = (
    "a natural, relaxed British football commentator with a modern, neutral British accent. Sound like "
    "a real person chatting on a football podcast, not a newsreader and not an impression. Keep a brisk, "
    "conversational pace (about 170 words a minute): never slow, never drawn out, no long pauses. "
    "Lift your energy a little for goals and big moments."
)
# try "ballad" or "verse" if you prefer
GPT_AUDIO_VOICE = os.getenv("AZURE_AUDIO_VOICE", "ash")

# ---- engine 2: Azure AI Speech ----
LANGUAGE_VOICES = {
    "Spanish": ("es-ES", "es-ES-AlvaroNeural"),
    "French": ("fr-FR", "fr-FR-HenriNeural"),
    "Portuguese": ("pt-BR", "pt-BR-AntonioNeural"),
    "Arabic": ("ar-SA", "ar-SA-HamedNeural"),
    "Hindi": ("hi-IN", "hi-IN-MadhurNeural"),
    "Japanese": ("ja-JP", "ja-JP-KeitaNeural"),
}
# fallback: natural British male, quicker pace
ENGLISH_VOICE = ("en-GB-RyanNeural", "+14%", "+0%")
AUDIO_MODEL_LANGUAGES = {"English", "Spanish",
                         "French", "Portuguese", "Arabic", "Hindi", "Japanese"}


def voice_for(language, personality):
    """Returns a description of the voice to use, or None if this language has no voice."""
    if os.getenv("AZURE_AUDIO_DEPLOYMENT") and language in AUDIO_MODEL_LANGUAGES:
        return ("gpt-audio", GPT_AUDIO_VOICE, NATURAL_DIRECTION)
    if language == "English":
        return ("speech", "en-GB") + ENGLISH_VOICE
    if language in LANGUAGE_VOICES:
        locale, v = LANGUAGE_VOICES[language]
        return ("speech", locale, v, "+10%", "+0%")
    return None


def _cached(key):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIO_DIR / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".mp3")


def _speak_gpt_audio(text, language, voice, direction):
    from openai import AzureOpenAI
    client = AzureOpenAI(azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                         api_key=os.environ["AZURE_OPENAI_API_KEY"],
                         api_version=os.getenv(
                             "AZURE_AUDIO_API_VERSION", "2025-01-01-preview"),
                         max_retries=4, timeout=60)
    accent = "" if language == "English" else f" Speak in {language} with a natural native accent."
    completion = client.chat.completions.create(
        model=os.environ["AZURE_AUDIO_DEPLOYMENT"],
        modalities=["text", "audio"],
        audio={"voice": voice, "format": "mp3"},
        messages=[
            {"role": "system", "content":
                f"You are a voice actor doing live football commentary. Perform as {direction}.{accent} "
                "Read the user's line out loud EXACTLY as written, word for word. Do not add, remove or "
                "change any words, and do not say anything else."},
            {"role": "user", "content": text},
        ],
    )
    return base64.b64decode(completion.choices[0].message.audio.data)


def _speak_azure_speech(text, locale, name, rate, pitch):
    key = os.getenv("AZURE_SPEECH_KEY") or os.getenv("AZURE_OPENAI_API_KEY")
    region = os.getenv("AZURE_SPEECH_REGION")
    if region:
        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    else:
        base = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/").replace(
            ".openai.azure.com", ".cognitiveservices.azure.com")
        url = f"{base}/cognitiveservices/v1"
    ssml = (f"<speak version='1.0' xml:lang='{locale}'><voice xml:lang='{locale}' name='{name}'>"
            f"<prosody rate='{rate}' pitch='{pitch}'>{escape(text)}</prosody></voice></speak>")
    r = requests.post(url, data=ssml.encode("utf-8"), timeout=30, headers={
        "Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "matchday-social-desk"})
    if r.status_code != 200:
        raise RuntimeError(
            f"Azure Speech returned {r.status_code}: {r.text[:200]}")
    return r.content


def speak(text, language="English", personality="standard"):
    """Turn text into commentary audio (MP3 bytes). Cached on disk. Returns None on failure."""
    if os.getenv("MOCK_AI") == "1" or not text:
        return None
    voice = voice_for(language, personality)
    if voice is None:
        return None
    path = _cached("|".join(map(str, voice)) + "|" + language + "|" + text)
    if path.exists():
        return path.read_bytes()
    audio = None
    if voice[0] == "gpt-audio":
        try:
            audio = _speak_gpt_audio(text, language, voice[1], voice[2])
        except Exception as e:
            print(
                f"[voice] gpt-audio-mini failed, using Azure Speech instead: {e}")
            fallback_lang = language if language in LANGUAGE_VOICES or language == "English" else "English"
            if language == "English":
                voice = ("speech", "en-GB") + ENGLISH_VOICE
            elif language in LANGUAGE_VOICES:
                locale, v = LANGUAGE_VOICES[fallback_lang]
                voice = ("speech", locale, v, "+0%", "+0%")
            else:
                return None
    if audio is None:
        try:
            audio = _speak_azure_speech(text, *voice[1:])
        except Exception as e:
            print(f"[voice] {e}")
            return None
    path.write_bytes(audio)
    return audio


def estimate_seconds(text, personality="standard"):
    """Rough speaking time, so the app can hold the caption while it's read out."""
    words = len(str(text).split())
    rate = 2.9                                  # brisk commentary pace
    return 1.0 + words / rate


if __name__ == "__main__":
    line = ("And Darnell Hayes has only gone and done it! North West Wonders are in front, and "
            "honestly, they've deserved that.")
    v = voice_for("English", "standard")
    print(
        f"Using: {'gpt-audio-mini (natural voice)' if v[0] == 'gpt-audio' else 'Azure Speech'}")
    audio = speak(line, "English", "standard")
    if audio:
        Path("output").mkdir(exist_ok=True)
        Path("output/voice_test.mp3").write_bytes(audio)
        print("It works! Open output/voice_test.mp3 to listen.")
    else:
        print("No audio. See the [voice] message above for the reason.")
