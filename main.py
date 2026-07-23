import os
import sys
import time
import queue
import json
import logging
import threading
import tkinter as tk
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
from audio.tts import text_to_speech

# Thread-safe communications queue
input_queue = queue.Queue()
detector = None
gui = None
voice_capture_lock = threading.Lock()

def run_brain_interaction(session_id, prompt, gui, speak_response=False):
    """
    Coordinates multi-turn tool calling and final response text-to-speech.
    """
    current_prompt = prompt
    max_turns = 5
    turn = 0
    
    while turn < max_turns:
        turn += 1
        # 1. Ask brain
        logger.info(f"Querying brain router. Turn: {turn}")
        role = "user" if turn == 1 else "tool"
        response = ask(session_id, current_prompt, role=role)
        logger.info(f"Brain router response: type={response.get('type')}, model={response.get('model')}")
        
        # 2. Save model's raw response in SQLite messages history
        save_message(session_id, "assistant", json.dumps(response))
        
        if response["type"] == "text":
            # Direct text response
            content = response["content"]
            gui.add_message("assistant", content)
            
            # Text chat remains silent. TTS runs only after the user explicitly
            # started a push-to-talk interaction from the GUI.
            if speak_response:
                gui.set_state("Speaking")
                try:
                    text_to_speech(content, out_path="reply.mp3")
                except Exception as e:
                    logger.error(f"TTS execution failed: {e}")
                    gui.add_message("system", f"TTS failed: {e}")
            break
            
        elif response["type"] == "tool_call":
            # Tool invocation requested
            tool_name = response["tool_name"]
            tool_args = response["tool_args"]
            
            gui.add_message("system", f"Invoking tool '{tool_name}'…")
            
            # Execute tool (dispatch handles security confirmation dialog and logs)
            tool_res = dispatch(tool_name, tool_args)
            
            # Log results in chat log
            if tool_res["status"] == "denied":
                gui.add_message("tool", f"Tool '{tool_name}' execution denied by user.")
            elif not tool_res["success"]:
                gui.add_message("tool", f"Tool '{tool_name}' failed: {tool_res['error']}\nOutput: {tool_res['output']}")
            else:
                gui.add_message("tool", f"Tool '{tool_name}' output:\n{tool_res['output']}")
                
            # Construct tool feedback message
            tool_msg_content = {
                "tool_name": tool_name,
                "result": tool_res["output"] if tool_res["success"] else f"Error: {tool_res['error']}",
                "status": tool_res["status"]
            }
            save_message(session_id, "tool", json.dumps(tool_msg_content))

            # A failed tool result is already actionable feedback for the user.
            # Continuing to ask the model can make it repeat the same call until
            # max_turns is reached (for example, when an online service is down).
            if not tool_res["success"]:
                gui.add_message(
                    "assistant",
                    f"I couldn't complete that because {tool_res['error']}. Please try again shortly."
                )
                break
            
            # Feed result back as next prompt to continue assistant cycle
            current_prompt = json.dumps(tool_msg_content)
            gui.set_state("Thinking")

def assistant_loop(gui, detector):
    """
    Background worker thread running the continuous coordinator loop in text-only mode.
    """
    session_id = "default_session"
    logger.info("Assistant coordinator loop started in TEXT-ONLY mode (STT and VAD disabled).")
    
    gui.set_state("Idle")
    
    while True:
        # Block until we receive a text input from the GUI entry
        item = input_queue.get(block=True)
        if item["type"] == "text":
            user_text = item["content"]
            speak_response = item.get("voice", False)
            
            logger.info(f"Processing query: '{user_text}'")
            gui.add_message("user", user_text)
            save_message(session_id, "user", user_text)
            
            gui.set_state("Thinking")
            try:
                run_brain_interaction(session_id, user_text, gui, speak_response=speak_response)
            except Exception as e:
                logger.error(f"Error in brain interaction: {e}")
                gui.add_message("system", f"*Error processing request: {e}*")
                gui.set_state("Error")
                time.sleep(2)
            
            gui.set_state("Idle")
        else:
            time.sleep(0.1)

def on_text_submit(text):
    """
    Callback fired when user submits text via typed GUI entry.
    """
    logger.info(f"User submitted typed text: {text}")
    # Stop the VAD arecord recording subprocess immediately to unblock recorder thread
    if detector:
        detector.stop_recording()
    # Put input into queue
    input_queue.put({"type": "text", "content": text})


def _get_voice_detector():
    """Create the microphone/VAD stack only after the user activates voice mode."""
    global detector
    if detector is None:
        logger.info("Initializing voice detector after GUI activation...")
        detector = VoiceDetector()
    return detector


def _capture_voice_input():
    """Record one utterance, transcribe it, and queue it as a voice interaction."""
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
            gui.add_message("system", "No speech detected. Tap 🎙 Speak and try again.")
            return

        gui.set_state("Transcribing")
        transcript = speech_to_text(recorded_path).strip()
        if not transcript:
            gui.add_message("system", "I couldn't transcribe that audio. Please try again.")
            return
        input_queue.put({"type": "text", "content": transcript, "voice": True})
    except Exception as e:
        logger.error(f"Voice interaction failed: {e}")
        gui.add_message("system", f"Voice input failed: {e}")
        gui.set_state("Error")
    finally:
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                logger.warning("Could not remove temporary voice recording: %s", audio_path)
        voice_capture_lock.release()
        gui.set_voice_capture_active(False)


def on_voice_activate():
    """GUI callback: start one microphone capture if one is not already active."""
    if not voice_capture_lock.acquire(blocking=False):
        return
    threading.Thread(target=_capture_voice_input, daemon=True).start()

def main():
    global detector, gui
    
    logger.info("Initializing SQLite Database...")
    init_db()
    
    logger.info("Launching GUI...")
    root = tk.Tk()
    gui = HarvisGUI(
        root,
        on_text_submit_callback=on_text_submit,
        on_voice_activate_callback=on_voice_activate,
    )
    
    # Register the thread-safe GUI confirmation callback on our system tools
    set_confirmation_callback(gui.request_confirmation_safely)
    
    # Spawn background worker thread
    bg_thread = threading.Thread(
        target=assistant_loop,
        args=(gui, detector),
        daemon=True
    )
    bg_thread.start()
    
    # Run the Tkinter main loop (must run in the main thread)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Exiting...")
    finally:
        if detector:
            detector.stop_recording()

if __name__ == "__main__":
    main()
