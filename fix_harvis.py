#!/usr/bin/env python3
# Fix gui.py and weather.py

# ===== FIX 1: gui.py =====
with open("gui.py", "r") as f:
    lines = f.readlines()

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]

    # Add KeyPress binding after Return binding
    if 'self.input_entry.bind("<Return>", lambda e: self.on_send())' in line:
        new_lines.append(line)
        indent = len(line) - len(line.lstrip())
        bind_line = ' ' * indent + 'self.input_entry.bind("<KeyPress>", self._on_entry_keypress)\n'
        if bind_line not in ''.join(lines):
            new_lines.append(bind_line)
        i += 1
        continue

    # Add _on_entry_keypress after _on_entry_focus_out
    if 'def _on_entry_focus_out(self, event=None):' in line:
        new_lines.append(line)
        i += 1
        # Copy the whole method body
        while i < len(lines) and (lines[i].strip() == '' or lines[i].startswith('        ')):
            new_lines.append(lines[i])
            i += 1
        # Add new method with blank line before
        new_lines.append('\n')
        new_lines.append('    def _on_entry_keypress(self, event=None):\n')
        new_lines.append('        if self._placeholder_active:\n')
        new_lines.append('            self.entry_var.set("")\n')
        new_lines.append('            self.input_entry.config(fg=self.text_color)\n')
        new_lines.append('            self._placeholder_active = False\n')
        continue

    new_lines.append(line)
    i += 1

with open("gui.py", "w") as f:
    f.writelines(new_lines)
print("Fixed gui.py")

# ===== FIX 2: weather.py =====
with open("tools/plugins/weather.py", "r") as f:
    text = f.read()

# Make weather keywords optional in regex
text = text.replace(
    'r"(?:weather|forecast|temperature|how\\s+(?:hot|cold|warm))(?:\\s+(?:in|for|at|like|is|it))?\\s+(.+)"',
    'r"(?:weather|forecast|temperature|how\\s+(?:hot|cold|warm))?(?:\\s+(?:in|for|at|like|is|it))?\\s*(.+)"'
)

# Fix empty match check
text = text.replace(
    '    if not match:',
    '    if not match or not match.group(1).strip():'
)

with open("tools/plugins/weather.py", "w") as f:
    f.write(text)
print("Fixed weather.py")
