"""TTS using custom API.

Endpoint: https://apis.davidcyril.name.ng/tools/speechma
"""

import os
import re
import urllib.parse
import logging
import subprocess
import tempfile
import requests

logger = logging.getLogger("harvis.tts")

def clean_text_for_tts(text: str) -> str:
    """Strip markdown/icons/emojis before sending to API."""
    if not text:
        return ""
    # We strip complex markdown but can leave basic punctuation
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r':[a-zA-Z0-9_]+:', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*---+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = text.replace('|', ' ')
    text = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]+', '', text)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def text_to_speech(text: str, out_path: str = "reply.mp3") -> bool:
    """
    Main entry point: clean text, fetch from API, and play with ffplay.
    Returns True on success.
    """
    clean_text = clean_text_for_tts(text)
    if not clean_text:
        logger.warning("No speakable text after cleaning.")
        return False
        
    try:
        logger.info("Using Speechma TTS API...")
        encoded_text = urllib.parse.quote(clean_text)
        url = f"https://apis.davidcyril.name.ng/tools/speechma?text={encoded_text}&voice=Andrew%20Multilingual&pitch=2&rate=1"
        
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        
        with open(tmp_path, "wb") as f_out:
            for chunk in response.iter_content(chunk_size=8192):
                f_out.write(chunk)
                
        logger.info(f"Audio saved to {tmp_path}, playing...")
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", tmp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        os.remove(tmp_path)
        return True
    except Exception as e:
        logger.error(f"Speechma TTS failed: {e}")
        return False


def unload():
    """No-op."""
    pass