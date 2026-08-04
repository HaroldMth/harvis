import os
import requests
import logging
from config import POLLINATIONS_API_KEY, GROQ_API_KEY
from db.store import update_provider_status

logger = logging.getLogger("harvis.stt")

def transcribe_pollinations(audio_file_path):
    if not POLLINATIONS_API_KEY:
        raise ValueError("Pollinations API key is not configured.")
    
    url = "https://gen.pollinations.ai/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {POLLINATIONS_API_KEY}"
    }
    
    with open(audio_file_path, "rb") as f:
        files = {
            "file": (os.path.basename(audio_file_path), f, "audio/mpeg")
        }
        data = {
            "model": "whisper"
        }
        
        response = requests.post(url, headers=headers, files=files, data=data, timeout=10)
        
    if response.status_code == 200:
        res_json = response.json()
        if "text" in res_json:
            return res_json["text"]
        else:
            raise ValueError(f"Unexpected response structure: {res_json}")
    else:
        raise RuntimeError(f"Pollinations STT failed with status code {response.status_code}: {response.text}")

def transcribe_groq(audio_file_path):
    if not GROQ_API_KEY:
        raise ValueError("Groq API key is not configured.")
        
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }
    
    with open(audio_file_path, "rb") as f:
        files = {
            "file": (os.path.basename(audio_file_path), f, "audio/mpeg")
        }
        data = {
            "model": "whisper-large-v3"
        }
        
        response = requests.post(url, headers=headers, files=files, data=data, timeout=10)
        
    if response.status_code == 200:
        res_json = response.json()
        if "text" in res_json:
            return res_json["text"]
        else:
            raise ValueError(f"Unexpected response structure: {res_json}")
    else:
        raise RuntimeError(f"Groq STT failed with status code {response.status_code}: {response.text}")

def speech_to_text(audio_file_path):
    """
    Transcribes audio_file_path using STT provider chain:
    1. Pollinations
    2. Groq Whisper
    """
    # 1. Pollinations
    try:
        text = transcribe_pollinations(audio_file_path)
        update_provider_status("pollinations_stt", True)
        return text
    except Exception as e:
        logger.warning(f"Pollinations STT failed: {e}")
        update_provider_status("pollinations_stt", False)
        
    # 2. Groq Whisper
    try:
        text = transcribe_groq(audio_file_path)
        update_provider_status("groq_stt", True)
        return text
    except Exception as e:
        logger.error(f"Groq STT failed: {e}")
        update_provider_status("groq_stt", False)
        
    raise RuntimeError("All STT providers failed.")
