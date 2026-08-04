"""STT using Puter API (OpenAI compatible)."""

import os
import logging
import subprocess

logger = logging.getLogger("harvis.stt")

def speech_to_text(audio_path: str = None) -> str:
    """Transcribe audio data to text using puter.js via node."""
    if not audio_path or not os.path.exists(audio_path):
        logger.error("No valid audio_path provided to speech_to_text.")
        return ""
        
    token = os.environ.get("PUTER_AUTH_TOKEN")
    if not token:
        logger.error("PUTER_AUTH_TOKEN not found in environment.")
        return ""
        
    logger.info(f"Transcribing file via Puter.js: {audio_path}")
    
    try:
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "puter_stt.mjs")
        env = os.environ.copy()
        env["PUTER_AUTH_TOKEN"] = token
        
        result = subprocess.run(
            ["node", script_path, audio_path],
            env=env,
            capture_output=True,
            text=True,
            check=True
        )
        
        text = result.stdout.strip()
        logger.info(f"Transcribed: '{text}'")
        return text
    except subprocess.CalledProcessError as e:
        logger.error(f"Transcription via Puter.js failed: {e.stderr}")
        return ""
    except Exception as e:
        logger.error(f"Transcription via Puter.js failed: {e}")
        return ""

def unload():
    """No-op for Puter API."""
    pass
