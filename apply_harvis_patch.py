#!/usr/bin/env python3
"""
HARVIS Enhancement Patch
========================
Applies all enhancements to your local HARVIS repo.

Run from ~/HARVIS:
    python3 apply_harvis_patch.py

Features applied:
- Extended Markdown rendering (headers, italics, code, lists, blockquotes, strikethrough, hr, icons)
- Material Symbols icon shortcode system (no emojis)
- Timestamps next to role labels
- Input placeholder text
- Dark custom scrollbar
- Themed confirmation dialog (replaces native messagebox)
- Idle CPU fix (200ms polling when idle)
- Plugin auto-discovery system
- Weather plugin (Open-Meteo, no API key)
- Name-addressing (Harold) in system prompt
- Word expressions instead of emojis
"""

import os
import shutil
import sys

PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
HARVIS_DIR = os.getcwd()

def backup(path):
    bak = path + ".bak"
    if os.path.exists(path) and not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f"  Backed up: {path}")

def copy_new(src, dst):
    if not os.path.exists(dst) or not os.path.samefile(src, dst): pass
    else: return
    shutil.copy2(src, dst)
    print(f"  Applied: {dst}")

def mkdir(path):
    os.makedirs(path, exist_ok=True)
    print(f"  Created dir: {path}")

def main():
    print("=" * 60)
    print("HARVIS Enhancement Patch")
    print("=" * 60)
    print(f"Target directory: {HARVIS_DIR}")
    print()

    # Verify we're in the right place
    if not os.path.exists(os.path.join(HARVIS_DIR, "main.py")):
        print("ERROR: Run this script from your ~/HARVIS directory.")
        sys.exit(1)

    # 1. Backup and apply gui.py
    gui_path = os.path.join(HARVIS_DIR, "gui.py")
    backup(gui_path)
    copy_new(os.path.join(PATCH_DIR, "gui.py"), gui_path)

    # 2. Backup and apply brain/prompts.py
    prompts_path = os.path.join(HARVIS_DIR, "brain", "prompts.py")
    backup(prompts_path)
    copy_new(os.path.join(PATCH_DIR, "prompts.py"), prompts_path)

    # 3. Backup and apply tools/registry.py
    registry_path = os.path.join(HARVIS_DIR, "tools", "registry.py")
    backup(registry_path)
    copy_new(os.path.join(PATCH_DIR, "registry.py"), registry_path)

    # 4. Create tools/plugins/ directory and files
    plugins_dir = os.path.join(HARVIS_DIR, "tools", "plugins")
    mkdir(plugins_dir)

    plugins_init_path = os.path.join(plugins_dir, "__init__.py")
    if not os.path.exists(plugins_init_path):
        copy_new(os.path.join(PATCH_DIR, "plugins_init.py"), plugins_init_path)
    else:
        backup(plugins_init_path)
        copy_new(os.path.join(PATCH_DIR, "plugins_init.py"), plugins_init_path)

    weather_path = os.path.join(plugins_dir, "weather.py")
    if os.path.exists(weather_path):
        backup(weather_path)
    copy_new(os.path.join(PATCH_DIR, "weather.py"), weather_path)

    # 5. Update providers.py to use get_all_tools_schema() instead of TOOLS_SCHEMA
    providers_path = os.path.join(HARVIS_DIR, "brain", "providers.py")
    if os.path.exists(providers_path):
        backup(providers_path)
        with open(providers_path, "r") as f:
            content = f.read()
        # Replace TOOLS_SCHEMA with get_all_tools_schema()
        if "from tools.registry import TOOLS_SCHEMA" in content:
            content = content.replace(
                "from tools.registry import TOOLS_SCHEMA",
                "from tools.registry import get_all_tools_schema"
            )
        if "TOOLS_SCHEMA" in content and "get_all_tools_schema()" not in content:
            content = content.replace("TOOLS_SCHEMA", "get_all_tools_schema()")
        with open(providers_path, "w") as f:
            f.write(content)
        print(f"  Patched: {providers_path} (TOOLS_SCHEMA -> get_all_tools_schema)")

    print()
    print("=" * 60)
    print("Patch applied successfully!")
    print()
    print("Next steps:")
    print("  1. Install Material Symbols font:")
    print("     sudo pacman -S ttf-material-symbols")
    print("  2. Test: python main.py")
    print("  3. If issues, restore from .bak files")
    print("=" * 60)

if __name__ == "__main__":
    main()
