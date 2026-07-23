import tkinter as tk
from tkinter import ttk, messagebox
import queue
import threading
import math
import time
import logging
import re
import webbrowser

logger = logging.getLogger("harvis.gui")

class HarvisGUI:
    def __init__(self, root, on_text_submit_callback=None, on_voice_activate_callback=None):
        self.root = root
        self.on_text_submit_callback = on_text_submit_callback
        self.on_voice_activate_callback = on_voice_activate_callback
        
        # Window settings
        self.root.title("HARVIS - Voice & Text Assistant")
        self.root.geometry("600x700")
        self.root.minsize(450, 600)
        
        # Configure colors (Modern Dark Theme)
        self.bg_color = "#121212"
        self.card_color = "#1E1E1E"
        self.accent_color = "#BB86FC"
        self.text_color = "#F3F4F6"
        self.input_bg = "#2D2D2D"
        self.muted_text = "#A8B0BE"
        
        self.root.configure(bg=self.bg_color)
        
        # Thread-safe confirmation queue
        self.confirm_queue = queue.Queue()
        self.root.bind("<<CheckConfirm>>", self.handle_confirm_request)
        
        # Setup layouts
        self.create_widgets()
        
        # Initial VAD/Assistant state
        self.current_state = "Idle"
        self.state_label.config(text="HARVIS · Idle")
        
        # Start animations
        self.animate_indicator()

    def create_widgets(self):
        # 1. Header Frame (Title & Glowing VAD Status)
        header_frame = tk.Frame(self.root, bg=self.bg_color, pady=15)
        header_frame.pack(fill="x", side="top", padx=20)
        
        # Indicator Canvas
        self.indicator_canvas = tk.Canvas(
            header_frame, 
            width=40, 
            height=40, 
            bg=self.bg_color, 
            highlightthickness=0
        )
        self.indicator_canvas.pack(side="left", padx=(0, 10))
        
        # Status Label
        self.state_label = tk.Label(
            header_frame,
            text="HARVIS · Ready",
            font=("Helvetica", 14, "bold"),
            fg=self.text_color,
            bg=self.bg_color
        )
        self.state_label.pack(side="left")
        
        # 2. Main Chat History (Text Widget with Scrollbar)
        chat_frame = tk.Frame(self.root, bg=self.bg_color)
        chat_frame.pack(fill="both", expand=True, padx=20, pady=5)
        
        # Custom scrollbar
        scrollbar = ttk.Scrollbar(chat_frame)
        scrollbar.pack(side="right", fill="y")
        
        self.chat_display = tk.Text(
            chat_frame,
            wrap="word",
            bg=self.card_color,
            fg=self.text_color,
            insertbackground=self.text_color,
            relief="flat",
            font=("Helvetica", 11),
            padx=15,
            pady=15,
            yscrollcommand=scrollbar.set
        )
        self.chat_display.pack(fill="both", expand=True, side="left")
        scrollbar.config(command=self.chat_display.yview)
        
        # Chat formatting: tags create simple, high-contrast message cards.
        self.chat_display.tag_config("user_label", foreground="#60A5FA", background="#1A2638", font=("Helvetica", 10, "bold"), spacing1=8)
        self.chat_display.tag_config("user_body", foreground=self.text_color, background="#1A2638", font=("Helvetica", 11), lmargin1=12, lmargin2=12, rmargin=12, spacing3=8)
        self.chat_display.tag_config("assistant_label", foreground="#C4B5FD", background="#251E38", font=("Helvetica", 10, "bold"), spacing1=8)
        self.chat_display.tag_config("assistant_body", foreground=self.text_color, background="#251E38", font=("Helvetica", 11), lmargin1=12, lmargin2=12, rmargin=12, spacing3=8)
        self.chat_display.tag_config("assistant_bold", foreground="#FFFFFF", background="#251E38", font=("Helvetica", 11, "bold"))
        self.chat_display.tag_config("tool_label", foreground="#5EEAD4", background="#102A25", font=("Helvetica", 10, "bold"), spacing1=8)
        self.chat_display.tag_config("tool_body", foreground="#B7F7DD", background="#102A25", font=("Courier New", 10), lmargin1=12, lmargin2=12, rmargin=12, spacing3=8)
        self.chat_display.tag_config("system", foreground=self.muted_text, font=("Helvetica", 10, "italic"), spacing1=7, spacing3=7)
        self.chat_display.tag_config("link", foreground="#93C5FD", underline=True)
        self.chat_display.configure(state="disabled")
        
        # 3. Input Frame (Text input box & Send Button)
        input_frame = tk.Frame(self.root, bg=self.bg_color, pady=15)
        input_frame.pack(fill="x", side="bottom", padx=20)
        
        self.entry_var = tk.StringVar()
        self.input_entry = tk.Entry(
            input_frame,
            textvariable=self.entry_var,
            bg=self.input_bg,
            fg=self.text_color,
            insertbackground=self.text_color,
            relief="flat",
            font=("Helvetica", 12),
            bd=0,
            highlightthickness=1,
            highlightcolor=self.accent_color,
            highlightbackground="#444444"
        )
        self.input_entry.pack(fill="both", expand=True, side="left", ipady=8, padx=(0, 10))
        self.input_entry.bind("<Return>", lambda e: self.on_send())
        
        # Placeholder or helper tooltip
        self.input_entry.focus()
        
        # Style Button
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Send.TButton",
            foreground="#FFFFFF",
            background="#BB86FC",
            font=("Helvetica", 11, "bold"),
            relief="flat",
            borderwidth=0
        )
        style.map(
            "Send.TButton",
            background=[("active", "#9E67E5"), ("pressed", "#7C4DFF")]
        )

        style.configure(
            "Voice.TButton",
            foreground="#D1FAE5",
            background="#16745B",
            font=("Helvetica", 11, "bold"),
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "Voice.TButton",
            background=[("active", "#0F8A6A"), ("disabled", "#3A4A45")],
        )

        self.voice_button = ttk.Button(
            input_frame,
            text="🎙  Speak",
            style="Voice.TButton",
            command=self.on_voice_activate,
            width=10,
        )
        self.voice_button.pack(side="right", fill="y", padx=(0, 10))
        
        send_btn = ttk.Button(
            input_frame,
            text="➤  Send",
            style="Send.TButton",
            command=self.on_send,
            width=8
        )
        send_btn.pack(side="right", fill="y")

    def on_send(self):
        text = self.entry_var.get().strip()
        if text and self.on_text_submit_callback:
            self.entry_var.set("")
            self.on_text_submit_callback(text)

    def on_voice_activate(self):
        """Start one explicit push-to-talk interaction; no background listening."""
        if self.on_voice_activate_callback:
            self.set_voice_capture_active(True)
            self.on_voice_activate_callback()

    def set_voice_capture_active(self, active):
        """Safely reflect microphone capture state from either GUI or worker threads."""
        def update():
            if active:
                self.voice_button.config(text="◼  Listening…", state="disabled")
            else:
                self.voice_button.config(text="🎙  Speak", state="normal")
        self.root.after(0, update)

    def set_state(self, state):
        """
        Updates UI state: Listening, Thinking, Speaking, Idle, Error
        """
        self.current_state = state
        self.root.after(0, lambda: self.state_label.config(text=f"HARVIS · {state}"))
        logger.info(f"GUI State set to: {state}")

    def _insert_link(self, label, url):
        """Insert a clickable link without opening anything until the user clicks it."""
        tag = f"link_{self.chat_display.index('end').replace('.', '_')}"
        self.chat_display.insert("end", label, ("assistant_body", "link", tag))
        self.chat_display.tag_bind(tag, "<Button-1>", lambda _event, target=url: webbrowser.open(target))
        self.chat_display.tag_bind(tag, "<Enter>", lambda _event: self.chat_display.config(cursor="hand2"))
        self.chat_display.tag_bind(tag, "<Leave>", lambda _event: self.chat_display.config(cursor=""))

    def _insert_assistant_text(self, text):
        """Render the small Markdown subset HARVIS commonly uses: bold and links."""
        pattern = re.compile(r"(\*\*(.+?)\*\*|\[([^\]]+)\]\((https?://[^\s)]+)\))")
        position = 0
        for match in pattern.finditer(text):
            if match.start() > position:
                self.chat_display.insert("end", text[position:match.start()], "assistant_body")
            if match.group(2) is not None:
                self.chat_display.insert("end", match.group(2), "assistant_bold")
            else:
                self._insert_link(match.group(3), match.group(4))
            position = match.end()
        if position < len(text):
            self.chat_display.insert("end", text[position:], "assistant_body")

    def _format_tool_message(self, text):
        """Convert raw tool logs into a compact, readable tool card."""
        match = re.match(r"Tool '([^']+)' (output|failed):\s*(.*)", text, re.DOTALL)
        if match:
            name, outcome, detail = match.groups()
            icons = {
                "web_search": "⌕", "fetch_url": "↗", "shell_exec": "⌨",
                "open_url": "◉", "open_app": "◉", "file_op": "▣",
                "system_info": "◌", "screenshot": "▣", "play_song": "♪",
            }
            icon = icons.get(name, "⚙")
            state = "COMPLETE" if outcome == "output" else "FAILED"
            return f"{icon}  {name.replace('_', ' ').upper()}  ·  {state}", detail
        if "denied" in text.lower():
            return "⚠  ACTION DENIED", text
        return "⚙  TOOL", text

    def add_message(self, role, text):
        """
        Thread-safe method to append message to display.
        """
        def update():
            self.chat_display.configure(state="normal")
            if role == "user":
                self.chat_display.insert("end", "👤  YOU\n", "user_label")
                self.chat_display.insert("end", f"{text}\n\n", "user_body")
            elif role == "assistant":
                self.chat_display.insert("end", "✦  HARVIS\n", "assistant_label")
                self._insert_assistant_text(text)
                self.chat_display.insert("end", "\n\n", "assistant_body")
            elif role == "tool":
                title, detail = self._format_tool_message(text)
                self.chat_display.insert("end", f"{title}\n", "tool_label")
                self.chat_display.insert("end", f"{detail}\n\n", "tool_body")
            else:
                clean_text = text.strip().strip("*")
                if clean_text.lower().startswith("invoking tool"):
                    clean_text = f"⌛  {clean_text.replace('Invoking tool', 'Working with', 1)}"
                self.chat_display.insert("end", f"{clean_text}\n\n", "system")
            self.chat_display.configure(state="disabled")
            self.chat_display.see("end")
            
        self.root.after(0, update)

    def animate_indicator(self):
        """
        Creates a pulsating light effect for VAD status.
        """
        if self.current_state in {"Listening", "Recording"}:
            color = "#10B981"  # Glowing green
            r = math.sin(time.time() * 5) * 3 + 12
        elif self.current_state == "Transcribing":
            color = "#F59E0B"  # Amber while speech is being converted to text
            r = math.sin(time.time() * 7) * 2 + 12
        elif self.current_state == "Thinking":
            color = "#3B82F6"  # Pulsing blue
            r = math.sin(time.time() * 8) * 3 + 12
        elif self.current_state == "Speaking":
            color = "#8B5CF6"  # Pulsing purple
            r = math.sin(time.time() * 6) * 3 + 12
        elif self.current_state == "Error":
            color = "#EF4444"  # Alert red
            r = math.sin(time.time() * 10) * 2 + 13
        else:
            color = "#4B5563"  # Idle gray
            r = 10
            
        self.indicator_canvas.delete("glow")
        self.indicator_canvas.create_oval(
            20 - r, 20 - r, 20 + r, 20 + r,
            fill=color,
            outline="",
            tags="glow"
        )
        
        # Run at 60 FPS (approx 16ms)
        self.root.after(20, self.animate_indicator)

    def request_confirmation_safely(self, message: str) -> bool:
        """
        Thread-safe method to show a confirmation dialog.
        Pushes request to a queue and blocks the calling (background) thread.
        """
        event = threading.Event()
        result_container = [False]
        
        req = {
            "message": message,
            "event": event,
            "result": result_container
        }
        
        self.confirm_queue.put(req)
        self.root.event_generate("<<CheckConfirm>>", when="tail")
        
        # Wait until GUI thread unblocks us
        event.wait()
        return result_container[0]

    def handle_confirm_request(self, event=None):
        """
        Runs in the GUI (main) thread. Displays modal confirmation.
        """
        try:
            while not self.confirm_queue.empty():
                req = self.confirm_queue.get_nowait()
                # Show standard message box (modal dialog)
                confirmed = messagebox.askyesno(
                    title="HARVIS Security Guardrail",
                    message=req["message"]
                )
                req["result"][0] = confirmed
                req["event"].set()
        except Exception as e:
            logger.error(f"Error in confirmation handler: {e}")
