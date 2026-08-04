import os
import sys
import time
import queue
import json
import logging
import threading
import tempfile

# Initialize basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("harvis.main")

# Add workspace directory to path to ensure correct imports
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from db.store import init_db, save_message
from audio.vad import VoiceDetector
from audio.stt import speech_to_text
from tools.system import set_confirmation_callback
from gui import HarvisGUI
from brain.router import ask
from tools.registry import dispatch
from audio.tts import text_to_speech, clean_text_for_tts

input_queue = queue.Queue()
detector = None
gui = None
voice_capture_lock = threading.Lock()

def run_brain_interaction(session_id, prompt, gui, speak_response=True):
    current_prompt = prompt
    max_turns = 5
    turn = 0
    
    while turn < max_turns:
        turn += 1
        logger.info(f"Querying brain router. Turn: {turn}")
        role = "user" if turn == 1 else "tool"
        response = ask(session_id, current_prompt, role=role)
        
        save_message(session_id, "assistant", json.dumps(response))
        
        if response["type"] == "text":
            content = response["content"]
            gui.add_message("assistant", content)
            
            if speak_response:
                gui.set_state("Speaking")
                try:
                    text_to_speech(clean_text_for_tts(content), out_path="reply.mp3")
                except Exception as e:
                    logger.error(f"TTS execution failed: {e}")
            break
            
        elif response["type"] == "tool_call":
            tool_name = response["tool_name"]
            tool_args = response["tool_args"]
            gui.add_message("system", f"Invoking tool '{tool_name}'…")
            
            tool_res = dispatch(tool_name, tool_args)
            tool_msg_content = {
                "tool_name": tool_name,
                "result": tool_res["output"] if tool_res["success"] else f"Error: {tool_res['error']}",
                "status": tool_res["status"]
            }
            save_message(session_id, "tool", json.dumps(tool_msg_content))

            if not tool_res["success"]:
                gui.add_message("assistant", f"I couldn't complete that because {tool_res['error']}.")
                break
            
            current_prompt = json.dumps(tool_msg_content)
            gui.set_state("Thinking")

def assistant_loop(gui, detector):
    session_id = "default_session"
    logger.info("Assistant coordinator loop started.")
    
    gui.set_state("Idle")
    
    while True:
        item = input_queue.get(block=True)
        if item["type"] == "text":
            user_text = item["content"]
            speak_response = item.get("voice", True) # Default to voice for F9 island
            
            logger.info(f"Processing query: '{user_text}'")
            save_message(session_id, "user", user_text)
            
            gui.set_state("Thinking")
            try:
                run_brain_interaction(session_id, user_text, gui, speak_response=speak_response)
            except Exception as e:
                logger.error(f"Error in brain interaction: {e}")
                gui.set_state("Error")
        else:
            time.sleep(0.1)

def _get_voice_detector():
    global detector
    if detector is None:
        detector = VoiceDetector()
    return detector

def _capture_voice_input():
    audio_path = None
    try:
        active_detector = _get_voice_detector()
        with tempfile.NamedTemporaryFile(prefix="harvis-voice-", suffix=".wav", delete=False) as audio_file:
            audio_path = audio_file.name
        
        recorded_path = active_detector.listen_and_record(
            out_path=audio_path,
            status_callback=gui.set_state,
        )
        if not recorded_path:
            gui.set_state("Idle")
            return

        gui.set_state("Thinking") # Transcribing state can just show as Thinking in new UI
        transcript = speech_to_text(recorded_path).strip()
        if not transcript:
            gui.set_state("Idle")
            return
            
        input_queue.put({"type": "text", "content": transcript, "voice": True})
    except Exception as e:
        logger.error(f"Voice interaction failed: {e}")
        gui.set_state("Error")
    finally:
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass
        voice_capture_lock.release()

def on_voice_activate():
    if not voice_capture_lock.acquire(blocking=False):
        return
    gui.set_state("Listening")
    threading.Thread(target=_capture_voice_input, daemon=True).start()

def ipc_listener_loop():
    import socket
    socket_path = "/tmp/byteai.sock"
    if os.path.exists(socket_path):
        os.remove(socket_path)
    
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.bind(socket_path)
        s.listen()
        # Set permissions so any user/process can trigger it if needed, 
        # though it's in /tmp/ so it's fine for the local user.
        os.chmod(socket_path, 0o666)
        
        while True:
            try:
                conn, _ = s.accept()
                with conn:
                    # Whenever a connection is made, we trigger voice activation
                    logger.info("IPC Trigger received! Activating voice.")
                    on_voice_activate()
            except Exception as e:
                logger.error(f"IPC listener error: {e}")

def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v

def main():
    global detector, gui
    
    logger.info("Initializing configuration and environment...")
    load_env()
    if not os.environ.get("PUTER_AUTH_TOKEN"):
        logger.info("Puter auth token missing. Launching local auth server...")
        from auth_server import acquire_puter_token
        acquire_puter_token()
    
    logger.info("Initializing SQLite Database...")
    init_db()
    
    logger.info("Launching BYTE AI GUI...")
    gui = HarvisGUI(on_voice_activate_callback=on_voice_activate)
    set_confirmation_callback(gui.request_confirmation_safely)
    
    bg_thread = threading.Thread(target=assistant_loop, args=(gui, detector), daemon=True)
    bg_thread.start()
    
    # Start global hotkey listener
    ipc_thread = threading.Thread(target=ipc_listener_loop, daemon=True)
    ipc_thread.start()
    
    try:
        gui.app.exec()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Exiting...")
    finally:
        if detector:
            detector.stop_recording()

if __name__ == "__main__":
    main()
