#!/usr/bin/env python3
"""LinkDesk for Windows, Mac and Linux — lets someone you trust see and control
this computer from any phone, tablet or browser. Run: python linkdesk.py"""
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import core
from core import APP, VERSION, fmt_id

BG, CARD, LINE = "#f4f6fb", "#ffffff", "#e3e8f2"
TEXT, MUTED, ACCENT = "#101828", "#667085", "#2457e6"
GREEN, RED, AMBER = "#12a150", "#d92d20", "#dc8a06"


# ------------------------------------------------------------------- chat
class ChatWindow:
    def __init__(self, app, who):
        self.app, self.who = app, who
        self.win = tk.Toplevel(app.root)
        self.win.title(f"Chat — {who}")
        self.win.geometry("360x420")
        self.win.configure(bg=BG)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.log = tk.Text(self.win, wrap="word", state="disabled", bg=CARD, fg=TEXT,
                           relief="flat", padx=10, pady=8, font=("Segoe UI", 10))
        self.log.tag_configure("me", foreground=ACCENT, font=("Segoe UI", 10, "bold"))
        self.log.tag_configure("them", foreground=GREEN, font=("Segoe UI", 10, "bold"))
        self.log.tag_configure("note", foreground=MUTED, font=("Segoe UI", 9, "italic"))
        self.log.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        row = tk.Frame(self.win, bg=BG)
        row.pack(fill="x", padx=10, pady=(0, 10))
        self.entry = ttk.Entry(row)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda e: self.send())
        ttk.Button(row, text="Send", style="Accent.TButton", command=self.send).pack(side="left", padx=(6, 0))
        self.hide()

    def show(self, focus=True):
        self.win.deiconify()
        self.win.lift()
        if focus:  # never steal keyboard focus on incoming messages: the remote user may be typing
            self.entry.focus_set()

    def hide(self):
        self.win.withdraw()

    def add(self, who, text, tag):
        self.log.configure(state="normal")
        stamp = time.strftime("%H:%M")
        if tag == "note":
            self.log.insert("end", f"{stamp}  {text}\n", "note")
        else:
            self.log.insert("end", f"{who} ", tag)
            self.log.insert("end", f"{stamp}\n{text}\n\n")
        self.log.configure(state="disabled")
        self.log.see("end")

    def send(self):
        text = self.entry.get().strip()
        if text and self.app.engine.send_chat(text):
            self.add("You", text, "me")
            self.entry.delete(0, "end")

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# --------------------------------------------- session bar (while controlled)
class HostBar:
    """Always-visible bar so the person knows their computer is being controlled."""

    def __init__(self, app, who):
        self.win = tk.Toplevel(app.root)
        self.win.title(f"{APP} — session")
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        self.win.configure(bg="#2a0f12")
        self.win.protocol("WM_DELETE_WINDOW", app.engine.disconnect)
        tk.Label(self.win, text=f"●  {who} is controlling this computer", bg="#2a0f12",
                 fg="#ff8a80", font=("Segoe UI", 10, "bold")).pack(side="left", padx=12, pady=8)
        for text, cmd, bg in (("Disconnect", app.engine.disconnect, RED),
                              ("Send file", app.pick_file, "#4a1d22"), ("Chat", app.open_chat, "#4a1d22")):
            tk.Button(self.win, text=text, command=cmd, bg=bg, fg="white", relief="flat", bd=0, padx=12,
                      pady=3, activebackground="#7a2a30", activeforeground="white",
                      font=("Segoe UI", 9, "bold")).pack(side="right", padx=4, pady=6)
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        self.win.geometry(f"+{(self.win.winfo_screenwidth() - w) // 2}+8")

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# ------------------------------------------------------------- main app
class App:
    def __init__(self):
        core.enable_dpi_awareness()
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.title(APP)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._style()
        self.conf = core.load_conf()
        self.hostbar = self.chat = self.request_win = None
        self.who = ""
        self._build()
        self.engine = core.Engine(self.conf, self)
        self.root.after(30, self._pump)

    # ---- thread-safe callbacks from the engine
    def post(self, fn, *args):
        self.q.put((fn, args))

    def on_status(self, state, detail):
        self.post(self._set_status, state, detail)

    def on_incoming_request(self, label, wrong_password):
        self.post(self._ask_accept, label, wrong_password)

    def on_request_cancelled(self):
        self.post(self._close_request)

    def on_session_started(self, label):
        self.post(self._session_started, label)

    def on_session_ended(self, reason):
        self.post(self._session_ended, reason)

    def on_chat(self, who, text):
        self.post(self._chat_in, text)

    def on_notice(self, text):
        self.post(self._notice, text)

    def _pump(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.root.after(30, self._pump)

    # ---- UI
    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure("TEntry", fieldbackground="white", bordercolor=LINE, lightcolor=LINE, padding=6)
        s.configure("TButton", padding=(12, 6), background="#eef2fa", bordercolor=LINE, font=("Segoe UI", 9))
        s.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT,
                    font=("Segoe UI", 10, "bold"))
        s.map("Accent.TButton", background=[("active", "#1b45bd"), ("disabled", "#9db3f0")])

    def _card(self, title):
        outer = tk.Frame(self.root, bg=LINE)
        outer.pack(fill="x", padx=18, pady=(0, 12))
        card = tk.Frame(outer, bg=CARD, padx=16, pady=14)
        card.pack(fill="both", padx=1, pady=1)
        tk.Label(card, text=title.upper(), bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        return card

    def _build(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 12))
        tk.Label(head, text=APP, bg=BG, fg=ACCENT, font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(head, text=f"v{VERSION} · free", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=6, pady=(6, 0))

        c1 = self._card("This computer's ID")
        row = tk.Frame(c1, bg=CARD)
        row.pack(fill="x", pady=(6, 2))
        self.id_label = tk.Label(row, text=fmt_id(self.conf["id"]), bg=CARD, fg=TEXT, font=("Consolas", 26, "bold"))
        self.id_label.pack(side="left")
        ttk.Button(row, text="Copy", command=self._copy_id).pack(side="right")
        self.status = tk.Label(c1, text="● Connecting…", bg=CARD, fg=AMBER, font=("Segoe UI", 9))
        self.status.pack(anchor="w", pady=(2, 0))
        tk.Label(c1, text="Share this ID with someone you trust. They open LinkDesk in any\n"
                          "browser (phone, tablet or computer) and type it in.\n"
                          "You'll be asked to accept every request.",
                 bg=CARD, fg=MUTED, justify="left", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
        prow = tk.Frame(c1, bg=CARD)
        prow.pack(fill="x", pady=(10, 0))
        self.pw_label = tk.Label(prow, bg=CARD, fg=TEXT, font=("Segoe UI", 9))
        self.pw_label.pack(side="left")
        ttk.Button(prow, text="Set password…", command=self._set_password).pack(side="right")

        c2 = self._card("Control another device")
        tk.Label(c2, text="Open LinkDesk in your browser and enter the other device's ID.",
                 bg=CARD, fg=TEXT, justify="left", wraplength=380, font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 8))
        brow = tk.Frame(c2, bg=CARD)
        brow.pack(fill="x")
        ttk.Button(brow, text="Open LinkDesk web", style="Accent.TButton",
                   command=lambda: webbrowser.open(core.WEB_URL)).pack(side="left")
        ttk.Button(brow, text="Copy link", command=self._copy_link).pack(side="left", padx=8)
        self.msg = tk.Label(c2, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9), wraplength=380, justify="left")
        self.msg.pack(anchor="w", pady=(8, 0))

        foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", padx=18, pady=(0, 14))
        tk.Label(foot, text="Direct, encrypted connection · no account needed", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left")
        self._refresh_labels()

    def _refresh_labels(self):
        on = bool(self.conf.get("pw_key"))
        self.pw_label.configure(text="Unattended access: " + ("ON (password set)" if on else "off"),
                                fg=GREEN if on else MUTED)

    def _set_status(self, state, detail):
        text, color = {"online": ("● Ready", GREEN), "connecting": ("● Connecting…", AMBER),
                       "offline": ("● Offline, retrying… " + (detail[:70] if detail else ""), RED)}[state]
        self.status.configure(text=text, fg=color)

    def _copy(self, value, note):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.msg.configure(text=note, fg=ACCENT)

    def _copy_id(self):
        self._copy(self.conf["id"], "ID copied.")

    def _copy_link(self):
        self._copy(core.WEB_URL, "Link copied: " + core.WEB_URL)

    # ---- dialogs
    def _ask_text(self, title, prompt):
        result = {}
        d = tk.Toplevel(self.root)
        d.title(title)
        d.configure(bg=CARD, padx=18, pady=14)
        d.transient(self.root)
        d.resizable(False, False)
        tk.Label(d, text=prompt, bg=CARD, fg=TEXT, justify="left", wraplength=320,
                 font=("Segoe UI", 9)).pack(anchor="w")
        entries = []
        for i in range(2):
            if i == 1:
                tk.Label(d, text="Repeat password", bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 0))
            e = ttk.Entry(d, width=42, show="•")
            e.pack(fill="x", pady=(6, 0))
            entries.append(e)
        err = tk.Label(d, text="", bg=CARD, fg=RED, font=("Segoe UI", 8))
        err.pack(anchor="w")

        def ok(_=None):
            a, b = (e.get() for e in entries)
            if a != b:
                err.configure(text="Passwords don't match.")
                return
            if a and len(a) < 8:
                err.configure(text="Use at least 8 characters.")
                return
            result["v"] = a
            d.destroy()

        row = tk.Frame(d, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Save", style="Accent.TButton", command=ok).pack(side="right")
        ttk.Button(row, text="Cancel", command=d.destroy).pack(side="right", padx=6)
        d.bind("<Return>", ok)
        entries[0].focus_set()
        d.grab_set()
        self.root.wait_window(d)
        return result.get("v")

    def _set_password(self):
        pw = self._ask_text("Unattended access",
                            "Set a password so you can connect to this computer without someone "
                            "clicking Accept. Leave empty and Save to turn it off.")
        if pw is not None:
            self.engine.set_password(pw)
            self._refresh_labels()

    def _ask_accept(self, label, wrong_password):
        self._close_request()
        d = self.request_win = tk.Toplevel(self.root)
        d.title(f"{APP} — incoming request")
        d.configure(bg=CARD, padx=20, pady=16)
        d.attributes("-topmost", True)
        d.resizable(False, False)
        tk.Label(d, text="Someone wants to control this computer", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(d, text=f"From: {label}", bg=CARD, fg=TEXT, font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 0))
        tk.Label(d, text="They will see your screen and can use your mouse and keyboard.\n"
                         "Only accept if you know and trust this person.",
                 bg=CARD, fg=MUTED, justify="left", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
        if wrong_password:
            tk.Label(d, text="They entered a wrong unattended password.", bg=CARD, fg=RED,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))
        timer = tk.Label(d, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 8))
        timer.pack(anchor="w", pady=(8, 0))
        row = tk.Frame(d, bg=CARD)
        row.pack(fill="x", pady=(10, 0))

        def answer(ok):
            self._close_request()
            threading.Thread(target=self.engine.answer, args=(ok,), daemon=True).start()

        ttk.Button(row, text="Accept", style="Accent.TButton", command=lambda: answer(True)).pack(side="right")
        ttk.Button(row, text="Decline", command=lambda: answer(False)).pack(side="right", padx=8)
        d.protocol("WM_DELETE_WINDOW", lambda: answer(False))
        deadline = time.time() + 30

        def count():
            if self.request_win is not d:
                return
            left = int(deadline - time.time())
            if left <= 0:
                answer(False)
                return
            timer.configure(text=f"Declines automatically in {left}s")
            d.after(500, count)

        count()
        d.lift()
        d.focus_force()
        self.root.bell()

    def _close_request(self):
        if self.request_win:
            try:
                self.request_win.destroy()
            except tk.TclError:
                pass
            self.request_win = None

    # ---- sessions
    def _session_started(self, label):
        self.who = label
        self.chat = ChatWindow(self, label)
        self.hostbar = HostBar(self, label)
        self.root.iconify()

    def _session_ended(self, reason):
        for w in (self.hostbar, self.chat):
            if w:
                w.destroy()
        self.hostbar = self.chat = None
        self.root.deiconify()
        self.msg.configure(text=f"Session ended. {reason}", fg=MUTED)

    def open_chat(self):
        if self.chat:
            self.chat.show()

    def pick_file(self):
        path = filedialog.askopenfilename(title="Choose a file to send")
        if path:
            self.engine.send_file(path)

    def _chat_in(self, text):
        if self.chat:
            self.chat.add(self.who, text, "them")
            self.chat.show(focus=False)

    def _notice(self, text):
        if self.chat:
            self.chat.add("", text, "note")
            if "Received" in text or "Could not" in text:
                self.chat.show(focus=False)
        else:
            self.msg.configure(text=text, fg=MUTED)

    def quit(self):
        if self.engine.session and self.engine.session.state == "live" and \
                not messagebox.askyesno(APP, "End the current session and quit?"):
            return
        self.engine.shutdown()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
