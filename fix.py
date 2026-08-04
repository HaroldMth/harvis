#!/usr/bin/env python3
# Fix gui.py and weather.py

import re

# ===== FIX 1: gui.py =====
with open("gui.py", "r") as f:
    lines = f.readlines()

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]

    # Add KeyPress binding after Return binding
    if 'self.input_entry.bind("<Return>", lambda e: self.on_send())' in line and '<KeyPress>' not in ''.join(lines):
        new_lines.append(line)
        indent = len(line) - len(line.lstrip())
        new_lines.append(' ' * indent + 'self.input_entry.bind("<KeyPress>", self._on_entry_keypress)
')
        i += 1
        continue

    # Add _on_entry_keypress after _on_entry_focus_out
    if 'def _on_entry_focus_out(self, event=None):' in line:
        new_lines.append(line)
        i += 1
        # Copy the whole method
        while i < len(lines) and (lines[i].strip() == '' or lines[i].startswith('        ')):
            new_lines.append(lines[i])
            i += 1
        # Add new method
        new_lines.append('
')
        new_lines.append('    def _on_entry_keypress(self, event=None):
')
        new_lines.append('        if self._placeholder_active:
')
        new_lines.append('            self.entry_var.set("")
')
        new_lines.append('            self.input_entry.config(fg=self.text_color)
')
        new_lines.append('            self._placeholder_active = False
')
        continue

    new_lines.append(line)
    i += 1

with open("gui.py", "w") as f:
    f.writelines(new_lines)
print("Fixed gui.py")

# ===== FIX 2: weather.py =====
with open("tools/plugins/weather.py", "r") as f:
    content = f.read()

# Make weather keywords optional
content = content.replace(
    'r"(?:weather|forecast|temperature|how\\s+(?:hot|cold|warm))(?:\\s+(?:in|for|at|like|is|it))?\\s+(.+)"',
    'r"(?:weather|forecast|temperature|how\\s+(?:hot|cold|warm))?(?:\\s+(?:in|for|at|like|is|it))?\\s*(.+)"'
)

# Fix empty match check
content = content.replace(
    '    if not match:',
    '    if not match or not match.group(1).strip():'
)

with open("tools/plugins/weather.py", "w") as f:
    f.write(content)
print("Fixed weather.py")
