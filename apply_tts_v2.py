#!/usr/bin/env python3
import os
import shutil

HARVIS_DIR = os.getcwd()

if not os.path.exists(os.path.join(HARVIS_DIR, "main.py")):
    print("ERROR: Run from ~/HARVIS")
    exit(1)

tts_path = os.path.join(HARVIS_DIR, "audio", "tts.py")
shutil.copy2(tts_path, tts_path + ".bak3")
shutil.copy2(os.path.join(os.path.dirname(__file__), "tts.py"), tts_path)
print("Applied tts.py v2 with fluent speech:")
print("  - Punctuation preserved for natural pauses")
print("  - Sentence-aware synthesis with gaps")
print("  - Eric voice (am_eric) by default")
print("  - Markdown stripped, punctuation kept")
