import os
import requests
import urllib.parse
import subprocess
import asyncio
import logging
from config import POLLINATIONS_API_KEY
from db.store import update_provider_status

logger = logging.getLogger("harvis.tts")

def play_audio(file_path):
    try:
        # Try mpv first
        res = subprocess.run(["which", "mpv"], capture_output=True, text=True)
        if res.returncode == 0:
            subprocess.run(["mpv", "--no-video", file_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception as e:
        logger.warning(f"mpv playback failed: {e}")

    try:
        # Fallback to paplay
        res = subprocess.run(["which", "paplay"], capture_output=True, text=True)
        if res.returncode == 0:
            if file_path.endswith(".mp3"):
                wav_path = file_path.replace(".mp3", ".wav")
                # convert using ffmpeg
                subprocess.run(["ffmpeg", "-y", "-i", file_path, wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                subprocess.run(["paplay", wav_path], check=True)
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            else:
                subprocess.run(["paplay", file_path], check=True)
            return True
    except Exception as e:
        logger.error(f"paplay playback failed: {e}")

    return False

def tts_pollinations(text, out_path="reply.mp3"):
    # URL encode the text for Pollinations TTS
    encoded_text = urllib.parse.quote(text)
    url = f"https://text.pollinations.ai/{encoded_text}?model=openai-audio&voice=alloy"
    headers = {}
    if POLLINATIONS_API_KEY:
        headers["Authorization"] = f"Bearer {POLLINATIONS_API_KEY}"
        
    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code == 200:
        with open(out_path, "wb") as f:
            f.write(response.content)
        return True
    else:
        raise RuntimeError(f"Pollinations TTS failed with status {response.status_code}: {response.text}")

async def tts_edgetts_async(text, out_path="reply.mp3"):
    import edge_tts
    voice = "en-US-GuyNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)
    return True

def tts_edgetts(text, out_path="reply.mp3"):
    try:
        # Use existing event loop if running, otherwise run new one
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
            
        if loop and loop.is_running():
            # In an active event loop, run thread-safely or task-safely
            import nest_asyncio
            nest_asyncio.apply()
            loop.run_until_complete(tts_edgetts_async(text, out_path))
        else:
            asyncio.run(tts_edgetts_async(text, out_path))
        return True
    except Exception as e:
        raise RuntimeError(f"Edge TTS failed: {e}")

def text_to_speech(text, out_path="reply.mp3"):
    """
    Converts text to speech and plays it.
    Chain:
    1. Pollinations
    2. Edge TTS
    """
    success = False
    
    # 1. Pollinations
    try:
        tts_pollinations(text, out_path)
        update_provider_status("pollinations_tts", True)
        success = True
    except Exception as e:
        logger.warning(f"Pollinations TTS failed: {e}")
        update_provider_status("pollinations_tts", False)

    # 2. Edge TTS
    if not success:
        try:
            tts_edgetts(text, out_path)
            update_provider_status("edge_tts", True)
            success = True
        except Exception as e:
            logger.error(f"Edge TTS failed: {e}")
            update_provider_status("edge_tts", False)

    if success:
        play_audio(out_path)
        return True
        
    raise RuntimeError("All TTS providers failed.")
