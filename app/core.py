"""
LinkDesk core (the computer that is being controlled).

No server of your own is needed: the free public PeerJS service introduces the
two devices, then screen, mouse, keyboard, chat and files travel directly
between them over an encrypted WebRTC connection (aiortc).

No GUI code here, so it can be tested headless; linkdesk.py puts a window on top.
All Engine callbacks to the UI (ui.on_*) are called from background threads.
"""
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import struct
import sys
import threading
import time

APP = "LinkDesk"
VERSION = "2.0.0"
PROTOCOL = 2
ID_PREFIX = "linkdesk-"

# Where the web controller lives (GitHub Pages). The build workflow fills this in.
WEB_URL = os.environ.get("LINKDESK_WEB", "https://YOUR-USERNAME.github.io/linkdesk/")
# Free public PeerJS service. Only used to introduce devices; no data passes through it.
DEFAULT_SIGNAL = {"host": "0.peerjs.com", "port": 443, "path": "/", "secure": True, "key": "peerjs"}
ICE_SERVERS = ["stun:stun.l.google.com:19302", "stun:global.stun.twilio.com:3478"]

CONF_DIR = os.path.join(os.path.expanduser("~"), ".linkdesk")
CONF_FILE = os.path.join(CONF_DIR, "config.json")
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "LinkDesk")

MAX_WIDTH = 1600
JPEG_QUALITY = 60
MAX_FPS = 15
FRAME_CHUNK = 16000      # bytes per WebRTC message (safe for every browser)
FILE_CHUNK = 12 * 1024
PBKDF2_ROUNDS = 200_000


# ----------------------------------------------------------------- helpers
def fmt_id(i):
    return f"{i[:3]} {i[3:6]} {i[6:]}" if i and len(i) == 9 else (i or "")


def new_id():
    return str(secrets.randbelow(9 * 10**8) + 10**8)


def load_conf():
    conf = {}
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            conf = json.load(f)
    except (OSError, ValueError):
        pass
    changed = False
    if not conf.get("id"):
        conf["id"], changed = new_id(), True
    if not isinstance(conf.get("signal"), dict):
        conf["signal"], changed = dict(DEFAULT_SIGNAL), True
    for old in ("relay", "token"):  # settings from version 1
        if conf.pop(old, None) is not None:
            changed = True
    if changed:
        save_conf(conf)
    return conf


def save_conf(conf):
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = CONF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=2)
    os.replace(tmp, CONF_FILE)
    try:
        os.chmod(CONF_FILE, 0o600)
    except OSError:
        pass


def derive_key(password, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ROUNDS)


def make_proof(key, nonce):
    return hmac.new(key, nonce.encode(), "sha256").hexdigest()


def platform_name():
    return {"win32": "Windows", "darwin": "Mac"}.get(sys.platform, "Linux")


def enable_dpi_awareness():
    """Windows: make mouse coordinates match real screen pixels."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# --------------------------------------------------------- input injection
_KEYS = {
    "Enter": "enter", "Backspace": "backspace", "Tab": "tab", "Escape": "esc", "Delete": "delete",
    " ": "space", "Spacebar": "space", "ArrowLeft": "left", "ArrowRight": "right", "ArrowUp": "up",
    "ArrowDown": "down", "Home": "home", "End": "end", "PageUp": "page_up", "PageDown": "page_down",
    "Insert": "insert", "CapsLock": "caps_lock", "NumLock": "num_lock", "PrintScreen": "print_screen",
    "Pause": "pause", "ContextMenu": "menu", "Shift": "shift", "Control": "ctrl", "Alt": "alt",
    "AltGraph": "alt_gr", "Meta": "cmd", "OS": "cmd", "Win": "cmd",
}
_KEYS.update({f"F{i}": f"f{i}" for i in range(1, 13)})
_RIGHT = {"ShiftRight": "shift_r", "ControlRight": "ctrl_r", "AltRight": "alt_r", "MetaRight": "cmd_r", "OSRight": "cmd_r"}


class Injector:
    """Replays the controller's mouse and keyboard on this computer."""

    def __init__(self, monitor):
        from pynput import keyboard, mouse
        self.Key, self.KeyCode = keyboard.Key, keyboard.KeyCode
        self.kb, self.mouse = keyboard.Controller(), mouse.Controller()
        self.monitor = monitor
        self.pressed = {}
        self.tapped = set()
        self.buttons = {"left": mouse.Button.left, "right": mouse.Button.right, "middle": mouse.Button.middle}

    def _pos(self, msg):
        m = self.monitor
        x = min(max(float(msg.get("x", 0)), 0.0), 1.0)
        y = min(max(float(msg.get("y", 0)), 0.0), 1.0)
        return int(m["left"] + x * (m["width"] - 1)), int(m["top"] + y * (m["height"] - 1))

    def mouse_event(self, msg):
        act = msg.get("act")
        if act in ("move", "down", "up", "scroll", "long"):
            self.mouse.position = self._pos(msg)
        if act == "down" and msg.get("b") in self.buttons:
            self.mouse.press(self.buttons[msg["b"]])
        elif act == "up" and msg.get("b") in self.buttons:
            self.mouse.release(self.buttons[msg["b"]])
        elif act == "long":  # long-press from a phone = right click
            self.mouse.click(self.buttons["right"])
        elif act == "scroll":
            self.mouse.scroll(int(msg.get("dx", 0)), int(msg.get("dy", 0)))

    def _resolve(self, key, code):
        name = _RIGHT.get(code) or _KEYS.get(key)
        if name:
            return getattr(self.Key, name, None)
        if len(key) == 1:
            return self.KeyCode.from_char(key.lower() if key.isalpha() else key)
        return None

    def key_event(self, msg):
        key, code = str(msg.get("key", "")), str(msg.get("code", ""))
        slot = code or key
        if msg.get("act") == "down":
            k = self._resolve(key, code)
            if k is None:
                return
            sym = getattr(k, "char", None)
            if sym and not sym.isalnum():
                # Symbols (< ! @ …): type the exact character with Shift lifted for a
                # moment, so different keyboard layouts can't turn "<" into ">".
                shifts = [v for s, v in self.pressed.items() if s.startswith("Shift")]
                for sh in shifts:
                    self.kb.release(sh)
                self.kb.press(k)
                self.kb.release(k)
                for sh in shifts:
                    self.kb.press(sh)
                self.tapped.add(slot)
                return
            self.pressed[slot] = k
            self.kb.press(k)
        else:
            if slot in self.tapped:
                self.tapped.discard(slot)
                return
            k = self.pressed.pop(slot, None) or self._resolve(key, code)
            if k is not None:
                try:
                    self.kb.release(k)
                except Exception:
                    pass

    def type_text(self, text):
        self.kb.type(text)

    def release_all(self):
        for k in list(self.pressed.values()):
            try:
                self.kb.release(k)
            except Exception:
                pass
        self.pressed.clear()


# -------------------------------------------------------- one live session
class Session:
    """A connected controller: authentication, then screen + input + chat + files."""

    def __init__(self, engine, pc, channel, label):
        self.engine, self.pc, self.ch = engine, pc, channel
        self.label = label                # e.g. "iPhone"
        self.state = "hello"              # hello -> auth -> asking -> live -> closed
        self.nonce = secrets.token_hex(16)
        self.ack = threading.Event()
        self.ack.set()
        self.streaming = False
        self.injector = None
        self.monitor = None
        self.incoming = {}
        self.frame_id = 0

    # ---- sending (any thread)
    def _send_now(self, data):
        try:
            if self.ch.readyState == "open":
                self.ch.send(data)
        except Exception:
            pass

    def send(self, obj):
        self.engine.call(self._send_now, json.dumps(obj))

    def send_frame(self, jpeg):
        self.frame_id = (self.frame_id + 1) & 0xFFFFFFFF
        parts = [jpeg[i:i + FRAME_CHUNK] for i in range(0, len(jpeg), FRAME_CHUNK)] or [b""]
        msgs = [b"F" + struct.pack(">IHH", self.frame_id, i, len(parts)) + p for i, p in enumerate(parts)]

        def go():
            for m in msgs:
                self._send_now(m)
        self.engine.call(go)

    # ---- incoming (networking thread)
    def on_message(self, data):
        if isinstance(data, bytes):
            return
        try:
            m = json.loads(data)
        except ValueError:
            return
        t = m.get("t")
        eng = self.engine
        if self.state == "hello" and t == "hello":
            self.label = str(m.get("name") or self.label)[:40]
            if eng.session is not None and eng.session is not self:
                self.send({"t": "denied", "reason": "The partner is busy in another session."})
                self.close()
                return
            eng.session = self
            self.state = "auth"
            self.send({"t": "challenge", "nonce": self.nonce, "salt": eng.conf.get("pw_salt", "")})
        elif self.state == "auth" and t == "auth":
            proof = str(m.get("proof", ""))
            if proof and eng.has_password and not eng.password_locked():
                good = make_proof(bytes.fromhex(eng.conf["pw_key"]), self.nonce)
                if hmac.compare_digest(good, proof):
                    threading.Thread(target=eng.answer, args=(True,), daemon=True).start()
                    return
                eng.bad_pw.append(time.time())
            self.state = "asking"
            eng.ui.on_incoming_request(self.label, bool(proof))
        elif self.state != "live":
            return
        elif t == "ack":
            self.ack.set()
        elif t == "mouse" and self.injector:
            eng.run_input(self.injector.mouse_event, m)
        elif t == "key" and self.injector:
            eng.run_input(self.injector.key_event, m)
        elif t == "text" and self.injector:
            eng.run_input(self.injector.type_text, str(m.get("s", ""))[:2000])
        elif t == "chat":
            eng.ui.on_chat(self.label, str(m.get("text", ""))[:4000])
        elif t in ("file_start", "file_chunk", "file_end"):
            self._file_msg(m)
        elif t == "bye":
            eng.end_session("The partner ended the session.", notify_peer=False)

    def _file_msg(self, m):
        t, fid = m["t"], str(m.get("fid", ""))
        try:
            if t == "file_start":
                name = os.path.basename(str(m.get("name", ""))).strip() or "file"
                os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                base, ext = os.path.splitext(name)
                path, n = os.path.join(DOWNLOAD_DIR, name), 1
                while os.path.exists(path):
                    path, n = os.path.join(DOWNLOAD_DIR, f"{base} ({n}){ext}"), n + 1
                self.incoming[fid] = (open(path, "wb"), path, name)
                self.engine.ui.on_notice(f"Receiving {name} ({_human(int(m.get('size', 0)))})…")
            elif t == "file_chunk" and fid in self.incoming:
                self.incoming[fid][0].write(base64.b64decode(m.get("data", "")))
            elif t == "file_end" and fid in self.incoming:
                f, path, name = self.incoming.pop(fid)
                f.close()
                self.engine.ui.on_notice(f"Received {name} → saved to {path}")
                self.send({"t": "note", "text": f"{name} was saved on the partner's computer."})
        except Exception as e:
            self.engine.ui.on_notice(f"File error: {e}")

    # ---- streaming (own thread)
    def start_streaming(self):
        import mss
        open_screen = getattr(mss, "MSS", None) or mss.mss  # mss 10 renamed the class
        with open_screen() as s:
            mons = s.monitors
            self.monitor = dict(mons[1] if len(mons) > 1 else mons[0])
        self.streaming = True
        threading.Thread(target=self._stream, args=(open_screen,), daemon=True).start()

    def _stream(self, open_screen):
        from PIL import Image
        interval = 1.0 / MAX_FPS
        last = None
        last_size = None
        try:
            with open_screen() as sct:
                while self.streaming:
                    t0 = time.time()
                    if not self.ack.wait(2.0):
                        last = None  # an acknowledgement got lost: send a fresh frame
                    if not self.streaming:
                        break
                    shot = sct.grab(self.monitor)
                    digest = hashlib.blake2b(shot.bgra, digest_size=16).digest()
                    if digest == last:
                        time.sleep(interval)
                        continue
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    if img.width > MAX_WIDTH:
                        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.BILINEAR)
                    if img.size != last_size:
                        last_size = img.size
                        self.send({"t": "screen", "w": img.width, "h": img.height})
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=JPEG_QUALITY)
                    self.ack.clear()
                    self.send_frame(buf.getvalue())
                    last = digest
                    spare = interval - (time.time() - t0)
                    if spare > 0:
                        time.sleep(spare)
        except Exception as e:
            self.engine.ui.on_notice(f"Screen capture stopped: {e}")

    def close(self):
        self.state = "closed"
        self.streaming = False
        self.ack.set()
        if self.injector:
            self.injector.release_all()
        for f, _, _ in self.incoming.values():
            try:
                f.close()
            except Exception:
                pass
        self.incoming.clear()
        pc = self.pc

        async def shut():
            await asyncio.sleep(0.4)  # let a final message go out
            await pc.close()
        self.engine.call(lambda: asyncio.ensure_future(shut()))


# ----------------------------------------------------------------- engine
class Engine:
    def __init__(self, conf, ui):
        self.conf, self.ui = conf, ui
        self.session = None
        self.pcs = set()
        self.bad_pw = []
        self.online = False
        self.stopped = False
        self.ws = None
        self.token = secrets.token_hex(8)
        self.loop = asyncio.new_event_loop()
        self.input_lock = threading.Lock()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.call(lambda: asyncio.ensure_future(self._signal_loop()))

    def call(self, fn, *args):
        """Run fn on the networking thread."""
        self.loop.call_soon_threadsafe(fn, *args)

    def run_input(self, fn, arg):
        with self.input_lock:
            try:
                fn(arg)
            except Exception as e:
                self.ui.on_notice(f"Input error: {e}")

    # ---------------- settings
    @property
    def peer_id(self):
        return ID_PREFIX + self.conf["id"]

    @property
    def has_password(self):
        return bool(self.conf.get("pw_key"))

    def set_password(self, password):
        if password:
            salt = secrets.token_hex(16)
            self.conf["pw_salt"], self.conf["pw_key"] = salt, derive_key(password, salt).hex()
        else:
            self.conf.pop("pw_salt", None)
            self.conf.pop("pw_key", None)
        save_conf(self.conf)

    def password_locked(self):
        now = time.time()
        self.bad_pw = [t for t in self.bad_pw if now - t < 600]
        return len(self.bad_pw) >= 5

    # ---------------- signaling (PeerJS protocol)
    def _signal_url(self):
        s = self.conf["signal"]
        scheme = "wss" if s.get("secure", True) else "ws"
        path = s.get("path", "/")
        path = path if path.endswith("/") else path + "/"
        return (f"{scheme}://{s['host']}:{s.get('port', 443)}{path}peerjs?key={s.get('key', 'peerjs')}"
                f"&id={self.peer_id}&token={self.token}&version=1.5.4")

    async def _signal_loop(self):
        from websockets.asyncio.client import connect
        delay = 1
        while not self.stopped:
            self.ui.on_status("connecting", "")
            try:
                async with connect(self._signal_url(), open_timeout=15, ping_interval=None,
                                   max_size=4 * 1024 * 1024) as ws:
                    self.ws = ws
                    hb = asyncio.ensure_future(self._heartbeat(ws))
                    try:
                        async for raw in ws:
                            try:
                                msg = json.loads(raw)
                            except ValueError:
                                continue
                            await self._on_signal(msg)
                    finally:
                        hb.cancel()
                    if not self.stopped:
                        self.ui.on_status("offline", "Connection service closed the link")
            except Exception as e:
                if not self.stopped:
                    self.ui.on_status("offline", str(e) or e.__class__.__name__)
            self.ws, self.online = None, False
            if self.stopped:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)
            self.token = secrets.token_hex(8)

    async def _heartbeat(self, ws):
        while True:
            await asyncio.sleep(5)
            await ws.send(json.dumps({"type": "HEARTBEAT"}))

    async def _signal_send(self, obj):
        if self.ws is not None:
            try:
                await self.ws.send(json.dumps(obj))
            except Exception:
                pass

    async def _on_signal(self, msg):
        t = msg.get("type")
        if t == "OPEN":
            self.online = True
            self.ui.on_status("online", "")
        elif t == "ID-TAKEN":
            # Usually our own previous connection that hasn't timed out yet.
            self.ui.on_status("offline", "ID still registered from a moment ago, retrying")
            await self.ws.close()
        elif t in ("ERROR", "INVALID-KEY"):
            self.ui.on_status("offline", str((msg.get("payload") or {}).get("msg", t)))
        elif t == "OFFER":
            try:
                await self._on_offer(msg)
            except Exception as e:
                self.ui.on_notice(f"Incoming connection failed: {e}")
        elif t == "CANDIDATE":
            await self._on_candidate(msg)

    async def _on_offer(self, msg):
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
        src, p = msg.get("src"), msg.get("payload") or {}
        if p.get("type") != "data" or not src:
            return
        conn_id = p.get("connectionId")
        label = str((p.get("metadata") or {}).get("name", "") or "Web browser")[:40]
        pc = RTCPeerConnection(RTCConfiguration([RTCIceServer(u) for u in ICE_SERVERS]))
        pc.ld_src, pc.ld_conn, pc.ld_pending, pc.ld_ready = src, conn_id, [], False
        self.pcs.add(pc)

        @pc.on("datachannel")
        def on_channel(ch):
            sess = Session(self, pc, ch, label)
            pc.ld_session = sess

            @ch.on("message")
            def on_msg(data):
                sess.on_message(data)

            @ch.on("close")
            def on_close():
                if self.session is sess:
                    self.end_session("The connection was closed.", notify_peer=False)

        @pc.on("connectionstatechange")
        async def on_state():
            if pc.connectionState in ("failed", "closed"):
                self.pcs.discard(pc)
                sess = getattr(pc, "ld_session", None)
                if sess is not None and self.session is sess:
                    self.end_session("The connection was lost.", notify_peer=False)
                if pc.connectionState == "failed":
                    await pc.close()

        sdp = p.get("sdp") or {}
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp.get("sdp", ""), type=sdp.get("type", "offer")))
        pc.ld_ready = True
        for c in pc.ld_pending:
            await self._add_candidate(pc, c)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)  # aiortc gathers all candidates into the answer
        await self._signal_send({"type": "ANSWER", "dst": src, "payload": {
            "sdp": {"type": "answer", "sdp": pc.localDescription.sdp}, "type": "data",
            "connectionId": conn_id, "browser": "linkdesk"}})

    async def _on_candidate(self, msg):
        p = msg.get("payload") or {}
        for pc in list(self.pcs):
            if pc.ld_conn == p.get("connectionId") and pc.ld_src == msg.get("src"):
                if pc.ld_ready:
                    await self._add_candidate(pc, p.get("candidate") or {})
                else:
                    pc.ld_pending.append(p.get("candidate") or {})

    @staticmethod
    async def _add_candidate(pc, c):
        from aiortc.sdp import candidate_from_sdp
        line = str(c.get("candidate", ""))
        if not line:
            return
        try:
            cand = candidate_from_sdp(line.split(":", 1)[1] if line.startswith("candidate:") else line)
            cand.sdpMid = c.get("sdpMid")
            cand.sdpMLineIndex = c.get("sdpMLineIndex")
            await pc.addIceCandidate(cand)
        except Exception:
            pass

    # ---------------- session control (any thread)
    def answer(self, accept):
        sess = self.session
        if sess is None or sess.state not in ("asking", "auth"):
            return
        if not accept:
            sess.send({"t": "denied", "reason": "The partner declined the request."})
            self.session = None
            sess.close()
            return
        try:
            sess.start_streaming()
        except Exception as e:
            sess.send({"t": "denied", "reason": f"Screen sharing failed on the partner's computer: {e}"})
            self.session = None
            sess.close()
            self.ui.on_notice(f"Could not share the screen: {e}")
            return
        try:
            sess.injector = Injector(sess.monitor)
        except Exception as e:  # e.g. no input permission yet: view-only
            self.ui.on_notice(f"Mouse/keyboard control unavailable: {e}")
        sess.state = "live"
        sess.send({"t": "accepted", "platform": platform_name(), "w": sess.monitor["width"],
                   "h": sess.monitor["height"], "control": sess.injector is not None})
        self.ui.on_session_started(sess.label)

    def end_session(self, reason="Session ended.", notify_peer=True):
        sess, self.session = self.session, None
        if sess is None:
            return
        was = sess.state
        if notify_peer:
            sess.send({"t": "bye", "reason": "The partner ended the session."})
        sess.close()
        if was == "asking":
            self.ui.on_request_cancelled()
        elif was == "live":
            self.ui.on_session_ended(reason)

    def disconnect(self):
        self.end_session("You ended the session.")

    def send_chat(self, text):
        s = self.session
        if s and s.state == "live" and text.strip():
            s.send({"t": "chat", "text": text[:4000]})
            return True
        return False

    def send_file(self, path):
        s = self.session
        if s and s.state == "live":
            threading.Thread(target=self._send_file, args=(s, path), daemon=True).start()

    def _send_file(self, s, path):
        name = os.path.basename(path)
        try:
            size = os.path.getsize(path)
            fid = secrets.token_hex(6)
            s.send({"t": "file_start", "fid": fid, "name": name, "size": size})
            with open(path, "rb") as f:
                while True:
                    if self.session is not s:
                        self.ui.on_notice(f"Sending {name} was cancelled.")
                        return
                    chunk = f.read(FILE_CHUNK)
                    if not chunk:
                        break
                    while getattr(s.ch, "bufferedAmount", 0) > (1 << 20):
                        time.sleep(0.03)
                    s.send({"t": "file_chunk", "fid": fid, "data": base64.b64encode(chunk).decode()})
                    time.sleep(0.002)
            s.send({"t": "file_end", "fid": fid})
            self.ui.on_notice(f"Sent {name} ({_human(size)}).")
        except Exception as e:
            self.ui.on_notice(f"Could not send {name}: {e}")

    def shutdown(self):
        self.end_session("App closed.")
        self.stopped = True

        async def stop():
            for pc in list(self.pcs):
                await pc.close()
            if self.ws is not None:
                await self.ws.close()
        try:
            asyncio.run_coroutine_threadsafe(stop(), self.loop).result(3)
        except Exception:
            pass
