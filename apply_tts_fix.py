#!/usr/bin/env python3
import os
import shutil

HARVIS_DIR = os.getcwd()

if not os.path.exists(os.path.join(HARVIS_DIR, "main.py")):
    print("ERROR: Run from ~/HARVIS")
    exit(1)

# Backup and replace tts.py
tts_path = os.path.join(HARVIS_DIR, "audio", "tts.py")
shutil.copy2(tts_path, tts_path + ".bak2")
shutil.copy2(os.path.join(os.path.dirname(__file__), "tts.py"), tts_path)
print("Applied tts.py with Eric voice + text cleaning")

# Patch main.py to clean text before TTS
main_path = os.path.join(HARVIS_DIR, "main.py")
with open(main_path, "r") as f:
    content = f.read()

# Add import if not present
if "from audio.tts import clean_text_for_tts" not in content:
    content = content.replace(
        "from audio.tts import text_to_speech",
        "from audio.tts import text_to_speech, clean_text_for_tts"
    )

# Wrap the TTS call with cleaning
if "clean_text_for_tts(content)" not in content:
    content = content.replace(
        'text_to_speech(content, out_path="reply.mp3")',
        'text_to_speech(clean_text_for_tts(content), out_path="reply.mp3")'
    )

with open(main_path, "w") as f:
    f.write(content)
print("Patched main.py to clean text before TTS")
print("\nDone! Eric voice (am_eric) with markdown/emoji stripping.")
