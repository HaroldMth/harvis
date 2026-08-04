#!/usr/bin/env python3
"""Apply local STT/TTS replacement to HARVIS."""

import os
import shutil

HARVIS_DIR = os.getcwd()
AUDIO_DIR = os.path.join(HARVIS_DIR, "audio")

if not os.path.exists(os.path.join(HARVIS_DIR, "main.py")):
    print("ERROR: Run from ~/HARVIS")
    exit(1)

# Backup old files
for f in ["stt.py", "tts.py"]:
    src = os.path.join(AUDIO_DIR, f)
    if os.path.exists(src):
        shutil.copy2(src, src + ".api.bak")
        print(f"Backed up: {src}")

# Copy new files
PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
for f in ["stt.py", "tts.py"]:
    src = os.path.join(PATCH_DIR, f)
    dst = os.path.join(AUDIO_DIR, f)
    shutil.copy2(src, dst)
    print(f"Applied: {dst}")

print("\nLocal STT/TTS applied!")
print("Models will lazy-load on first use and auto-unload after 60s idle.")
print("\nEnv vars you can set in ~/.tokens or shell:")
print("  HARVIS_STT_MODEL=base.en       # tiny, base, small, medium, large-v3")
print("  HARVIS_STT_COMPUTE=int8        # int8, float16, float32")
print("  HARVIS_KOKORO_VOICE=af_sarah   # see kokoro voices list")
print("  HARVIS_KOKORO_SPEED=1.0        # 0.5 to 2.0")
