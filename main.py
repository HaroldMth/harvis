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
from brain.modes import parse_slash, switch_message, status_message, DEFAULT_MODE
from tools.registry import dispatch
from audio.tts import text_to_speech, clean_text_for_tts

input_queue = queue.Queue()
detector = None
gui = None
voice_capture_lock = threading.Lock()
chat_mode = DEFAULT_MODE

def run_brain_interaction(session_id, prompt, gui, speak_response=True, mode="normal"):
    current_prompt = prompt
    max_turns = 5
    spoke = False
    role = "user"
    last_tool_name = None
    last_tool_output = ""

    for turn in range(max_turns):
        logger.info(f"Querying brain router. Turn: {turn + 1} mode={mode}")

        response = ask(session_id, current_prompt, role=role, mode=mode)
        
        save_message(session_id, "assistant", json.dumps(response))
        
        if response["type"] == "text":
            text_content = response["content"]
            gui.add_message("assistant", text_content)
            
            if speak_response and text_content.strip():
                gui.set_state("Speaking")
                try:
                    text_to_speech(clean_text_for_tts(text_content), out_path="reply.mp3")
                    spoke = True
                except Exception as e:
                    logger.error(f"TTS execution failed: {e}")
            break
            
        elif response["type"] == "tool_call":
            role = "tool"
            tool_name = response["tool_name"]
            tool_args = response.get("tool_args", {}) or {}
            last_tool_name = tool_name
            
            # Emit tool execution start to UI
            call_payload = {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "status": "running"
            }
            gui.add_message("tool_call", json.dumps(call_payload))
            
            tool_res = dispatch(tool_name, tool_args)
            last_tool_output = tool_res["output"] if tool_res["success"] else tool_res.get("error", "")
            
            # Emit tool execution completion/result to UI
            result_payload = {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "status": tool_res["status"],
                "output": tool_res["output"] if tool_res["success"] else f"Error: {tool_res['error']}"
            }
            gui.add_message("tool_result", json.dumps(result_payload))

            tool_msg_content = {
                "tool_name": tool_name,
                "result": tool_res["output"] if tool_res["success"] else f"Error: {tool_res['error']}",
                "status": tool_res["status"]
            }
            save_message(session_id, "tool", json.dumps(tool_msg_content))

            if not tool_res["success"]:
                err_msg = f"I couldn't complete that: {tool_res['error']}."
                gui.add_message("assistant", err_msg)
                if speak_response:
                    gui.set_state("Speaking")
                    text_to_speech(clean_text_for_tts(err_msg))
                    spoke = True
                break
            
            current_prompt = json.dumps(tool_msg_content)
            gui.set_state("Thinking")
    
    # If we used tools successfully but got no spoken confirmation, speak the actual tool result
    if speak_response and not spoke:
        gui.set_state("Speaking")
        if last_tool_name:
            confirm = f"{last_tool_name} completed. {last_tool_output[:100]}"
        else:
            confirm = "Task completed."
        text_to_speech(clean_text_for_tts(confirm))


def assistant_loop(gui, detector):
    global chat_mode
    session_id = "default_session"
    logger.info("Assistant coordinator loop started.")
    gui.set_state("Idle")
    gui.set_mode(chat_mode)

    while True:
        item = input_queue.get(block=True)
        if item["type"] == "text":
            user_text = item["content"]
            speak_response = item.get("voice", False)  # default no voice for typed

            parsed = parse_slash(user_text)
            if parsed:
                if parsed["kind"] == "status":
                    gui.add_message("system", status_message(chat_mode))
                    continue
                if parsed["kind"] == "set":
                    chat_mode = parsed["mode"]
                    gui.set_mode(chat_mode)
                    gui.add_message("system", switch_message(chat_mode))
                    logger.info(f"Chat mode set to {chat_mode}")
                    continue
                # oneshot: persist unchanged, this query only
                oneshot_mode = parsed["mode"]
                user_text = parsed["prompt"]
                logger.info(f"One-shot mode={oneshot_mode} (persisted={chat_mode})")
                gui.add_message("user", user_text)
                gui.set_state("Thinking")
                try:
                    run_brain_interaction(session_id, user_text, gui, speak_response=speak_response, mode=oneshot_mode)
                    gui.set_state("Idle")
                except Exception as e:
                    logger.error(f"Error in brain interaction: {e}")
                    gui.set_state("Error")
                continue

            logger.info(f"Processing query: '{user_text}' (mode={chat_mode})")
            gui.add_message("user", user_text)

            gui.set_state("Thinking")
            try:
                run_brain_interaction(session_id, user_text, gui, speak_response=speak_response, mode=chat_mode)
                gui.set_state("Idle")
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

def on_text_submit(text: str):
    """Called when user submits text from the chat UI."""
    if text:
        input_queue.put({"type": "text", "content": text, "voice": False})

def on_voice_activate():
    from audio.tts import stop_tts
    stop_tts()  # Stop speaking before recording

    if not voice_capture_lock.acquire(blocking=False):
        return
    gui.set_state("Listening")
    threading.Thread(target=_capture_voice_input, daemon=True).start()

def on_wakeword_detected(clean_prompt="", raw_text=""):
    from audio.tts import stop_tts
    stop_tts()

    logger.info(f"Wake word detected! Raw: '{raw_text}' | Clean: '{clean_prompt}'")
    if clean_prompt and len(clean_prompt) > 2:
        # User said "HARVIS <command>" in one sentence
        input_queue.put({"type": "text", "content": clean_prompt, "voice": True})
    else:
        # User just said "HARVIS!" → activate listening mode
        on_voice_activate()

def ipc_listener_loop():
    import socket
    socket_path = "/tmp/byteai.sock"
    if os.path.exists(socket_path):
        os.remove(socket_path)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.bind(socket_path)
        s.listen()
        os.chmod(socket_path, 0o666)

        while True:
            try:
                conn, _ = s.accept()
                with conn:
                    data = conn.recv(64).decode("utf-8", errors="ignore").strip()
                    logger.info(f"IPC trigger: '{data}'")
                    if data == "voice":
                        on_voice_activate()
                    else:
                        # Default: toggle window visibility
                        if gui:
                            gui.toggle_window()
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
    
    logger.info("Launching HARVIS GUI...")
    gui = HarvisGUI(
        on_text_submit_callback=on_text_submit,
        on_voice_activate_callback=on_voice_activate,
    )
    set_confirmation_callback(gui.request_confirmation_safely)
    
    bg_thread = threading.Thread(target=assistant_loop, args=(gui, detector), daemon=True)
    bg_thread.start()
    
    # Start global hotkey listener
    ipc_thread = threading.Thread(target=ipc_listener_loop, daemon=True)
    ipc_thread.start()

    # Start Ambient Wake Word Listener ("HARVIS" / "Hey HARVIS" / "FRIDAY" / "EDITH")
    try:
        from audio.wakeword import WakeWordListener
        ww_listener = WakeWordListener(callback=on_wakeword_detected)
        ww_listener.start()
    except Exception as e:
        logger.warning(f"Could not start Ambient Wake Word listener: {e}")
    
    try:
        gui.app.exec()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Exiting...")
    finally:
        if detector:
            detector.stop_recording()

if __name__ == "__main__":
    main()
