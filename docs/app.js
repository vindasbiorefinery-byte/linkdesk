/* LinkDesk web controller — control a computer or Android phone from any browser.
   Connects peer-to-peer (WebRTC). The free PeerJS service only introduces the two
   devices; screen, input, chat and files flow directly and encrypted between them. */
"use strict";

const ID_PREFIX = "linkdesk-";
const PROTOCOL = 2;
const ICE = [{ urls: "stun:stun.l.google.com:19302" }, { urls: "stun:global.stun.twilio.com:3478" }];
const PBKDF2_ROUNDS = 200000;
const FILE_CHUNK = 12 * 1024;

// ?signal=localhost:9000 lets you use your own PeerJS server (testing / self-hosting)
const params = new URLSearchParams(location.search);
function signalOptions() {
  const s = params.get("signal");
  if (!s) return { config: { iceServers: ICE }, debug: Number(params.get("debug") || 0) };
  const [host, port] = s.split(":");
  return { host, port: Number(port || 443), path: params.get("path") || "/", secure: params.get("secure") !== "0",
           key: params.get("key") || "peerjs", config: { iceServers: ICE }, debug: Number(params.get("debug") || 0) };
}

const $ = (id) => document.getElementById(id);
const els = {
  home: $("home"), session: $("session"), form: $("connectForm"), peerId: $("peerId"), peerPw: $("peerPw"),
  btn: $("connectBtn"), msg: $("msg"), recent: $("recent"), who: $("who"), stage: $("stage"), canvas: $("screen"),
  wait: $("wait"), kbd: $("kbd"), kbdBtn: $("kbdBtn"), chat: $("chat"), log: $("log"), chatForm: $("chatForm"),
  chatInput: $("chatInput"), navbar: $("navbar"), filePick: $("filePick"),
};
const ctx = els.canvas.getContext("2d");

let peer = null, conn = null, state = "idle", remote = null, password = "";
let screenW = 0, screenH = 0, platform = "";
const frames = new Map();
const incoming = new Map();

// ------------------------------------------------------------ helpers
const fmtId = (d) => d.replace(/\D/g, "").replace(/(\d{3})(\d{3})(\d{0,3})/, "$1 $2 $3").trim();
function setMsg(text, bad) { els.msg.textContent = text || ""; els.msg.classList.toggle("bad", !!bad); }
function toast(text) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = text;
  document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
}
function deviceName() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad|Macintosh/.test(ua) && navigator.maxTouchPoints > 1) return "iPad";
  if (/Android/.test(ua)) return /Mobile/.test(ua) ? "Android phone" : "Android tablet";
  if (/Windows/.test(ua)) return "Windows PC";
  if (/Mac/.test(ua)) return "Mac";
  if (/CrOS/.test(ua)) return "Chromebook";
  return "Web browser";
}
function send(obj) { if (conn && conn.open) conn.send(JSON.stringify(obj)); }
const hex = (buf) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
const unhex = (h) => new Uint8Array(h.match(/../g).map((x) => parseInt(x, 16)));

async function makeProof(pw, saltHex, nonce) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: unhex(saltHex), iterations: PBKDF2_ROUNDS }, base, 256);
  const key = await crypto.subtle.importKey("raw", bits, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(nonce)));
}

function b64encode(bytes) {
  let s = ""; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
function b64decode(str) { const s = atob(str); const out = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i); return out; }

// recent IDs (this browser only)
function recentList() { try { return JSON.parse(localStorage.getItem("ld_recent") || "[]"); } catch { return []; } }
function rememberId(id) {
  try { localStorage.setItem("ld_recent", JSON.stringify([id, ...recentList().filter((x) => x !== id)].slice(0, 5))); } catch {}
}
function renderRecent() {
  els.recent.replaceChildren(...recentList().map((id) => {
    const b = document.createElement("button"); b.type = "button"; b.textContent = fmtId(id);
    b.onclick = () => { els.peerId.value = fmtId(id); els.peerPw.focus(); };
    return b;
  }));
}

// ------------------------------------------------------------ connect
els.peerId.addEventListener("input", () => { els.peerId.value = fmtId(els.peerId.value); });
els.form.addEventListener("submit", (e) => { e.preventDefault(); connect(); });

function connect() {
  const id = els.peerId.value.replace(/\D/g, "");
  if (id.length !== 9) { setMsg("Enter the 9-digit ID shown in the partner's LinkDesk app.", true); return; }
  password = els.peerPw.value;
  remote = id; state = "connecting";
  els.btn.disabled = true; setMsg("Connecting…");
  const start = () => {
    conn = peer.connect(ID_PREFIX + id, { serialization: "raw", reliable: true, metadata: { name: deviceName() } });
    conn.on("open", () => { setMsg("Waiting for the partner to accept…"); send({ t: "hello", name: deviceName(), v: PROTOCOL }); });
    conn.on("data", onData);
    conn.on("close", () => endSession(state === "live" ? "The session ended." : "The partner declined or closed LinkDesk."));
    conn.on("error", (err) => endSession("Connection error: " + (err && err.type || err)));
    setTimeout(() => { if (state === "connecting" || state === "auth") endSession("No answer. Check the ID and that LinkDesk is open on the partner's device."); }, 45000);
  };
  if (peer && !peer.destroyed && !peer.disconnected && peer.open) start();
  else {
    if (peer) try { peer.destroy(); } catch {}
    peer = new Peer(undefined, signalOptions());
    peer.on("open", start);
    peer.on("error", (err) => {
      const t = err && err.type;
      if (t === "peer-unavailable") endSession("The partner is offline or the ID is wrong.");
      else if (t === "network" || t === "server-error" || t === "socket-error" || t === "socket-closed") endSession("Can't reach the connection service. Check your internet and try again.");
      else if (t === "browser-incompatible") endSession("This browser can't make direct connections. Try Chrome, Edge, Firefox or Safari.");
      else if (state !== "idle") endSession("Connection failed (" + t + ").");
    });
  }
}

async function onData(data) {
  if (typeof data !== "string") { onBinary(new Uint8Array(data)); return; }
  let m; try { m = JSON.parse(data); } catch { return; }
  switch (m.t) {
    case "challenge": {
      state = "auth";
      let proof = "";
      if (password && m.salt) { setMsg("Checking password…"); proof = await makeProof(password, m.salt, m.nonce); }
      send({ t: "auth", proof });
      setMsg(proof ? "Checking password…" : "Waiting for the partner to click Accept…");
      break;
    }
    case "accepted":
      state = "live"; platform = m.platform || ""; rememberId(remote);
      showSession(); setScreen(m.w, m.h);
      if (innerHeight > innerWidth && m.w > m.h) toast("Tip: turn your phone sideways, or tap 1× to zoom in.");
      if (m.control === false) toast("View only: the partner hasn't allowed remote control.");
      break;
    case "denied": endSession(m.reason || "The partner declined."); break;
    case "screen": setScreen(m.w, m.h); break;
    case "chat": addLog("them", fmtId(remote), m.text); openChat(); break;
    case "note": addNote(m.text); break;
    case "file_start": case "file_chunk": case "file_end": onFile(m); break;
    case "bye": endSession(m.reason || "The partner ended the session."); break;
  }
}

// ------------------------------------------------------------ screen
function onBinary(b) {
  if (b[0] !== 0x46 || b.length < 9) return; // 'F'
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const fid = dv.getUint32(1), idx = dv.getUint16(5), count = dv.getUint16(7);
  for (const k of frames.keys()) if (k !== fid) frames.delete(k);
  let f = frames.get(fid);
  if (!f) { f = { parts: new Array(count), got: 0 }; frames.set(fid, f); }
  if (!f.parts[idx]) { f.parts[idx] = b.subarray(9); f.got++; }
  if (f.got === count) { frames.delete(fid); drawFrame(new Blob(f.parts, { type: "image/jpeg" })); }
}
let drawing = false;
async function drawFrame(blob) {
  if (drawing) return; drawing = true;
  try {
    const bmp = await createImageBitmap(blob);
    if (els.canvas.width !== bmp.width || els.canvas.height !== bmp.height) { els.canvas.width = bmp.width; els.canvas.height = bmp.height; layout(); }
    ctx.drawImage(bmp, 0, 0); bmp.close && bmp.close();
    els.wait.hidden = true;
  } catch (e) { /* skip a bad frame */ }
  drawing = false;
  send({ t: "ack" });
}
function setScreen(w, h) { screenW = w || screenW; screenH = h || screenH; layout(); }
// Zoom (button cycles 1x/2x/3x). While zoomed, two-finger drag pans the view.
let zoom = 1, panX = 0.5, panY = 0.5;
function layout() {
  const sw = els.stage.clientWidth, sh = els.stage.clientHeight;
  const w = els.canvas.width > 2 ? els.canvas.width : (screenW || 16), h = els.canvas.width > 2 ? els.canvas.height : (screenH || 9);
  const s = Math.min(sw / w, sh / h) * zoom, W = w * s, H = h * s;
  const left = W <= sw ? (sw - W) / 2 : Math.min(0, Math.max(sw - W, sw / 2 - panX * W));
  const top = H <= sh ? (sh - H) / 2 : Math.min(0, Math.max(sh - H, sh / 2 - panY * H));
  Object.assign(els.canvas.style, { width: W + "px", height: H + "px", left: left + "px", top: top + "px" });
}
function panBy(dx, dy) {
  const r = els.canvas.getBoundingClientRect();
  panX = Math.min(1, Math.max(0, panX - dx / r.width)); panY = Math.min(1, Math.max(0, panY - dy / r.height)); layout();
}
$("zoomBtn").onclick = () => { zoom = zoom >= 3 ? 1 : zoom + 1; $("zoomBtn").textContent = zoom + "×"; layout(); };
addEventListener("resize", layout);

function showSession() {
  els.home.hidden = true; els.session.hidden = false;
  els.who.textContent = "● " + fmtId(remote) + (platform ? " · " + platform : "");
  els.navbar.hidden = platform.toLowerCase() !== "android";
  els.wait.hidden = false; els.wait.textContent = "Loading screen…";
  zoom = 1; panX = panY = 0.5; $("zoomBtn").textContent = "1×";
  requestAnimationFrame(layout);
}

function endSession(reason) {
  if (state === "idle") return;
  const wasLive = state === "live";
  state = "idle";
  try { if (conn && conn.open) send({ t: "bye" }); } catch {}
  try { conn && conn.close(); } catch {}
  conn = null; frames.clear();
  els.session.hidden = true; els.chat.hidden = true; els.home.hidden = false;
  els.btn.disabled = false; els.log.replaceChildren();
  ctx.clearRect(0, 0, els.canvas.width, els.canvas.height); els.canvas.width = els.canvas.height = 2;
  setMsg(reason, !wasLive);
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  renderRecent();
}
$("endBtn").onclick = () => endSession("You ended the session.");

// ------------------------------------------------------------ pointer input
function norm(e) {
  const r = els.canvas.getBoundingClientRect();
  return { x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1) };
}
const BTN = ["left", "middle", "right"];
let lastMove = 0;
const touches = new Map();
let touchMode = null, longTimer = 0, startPt = null, scrollAcc = 0, lastMid = null;

els.stage.addEventListener("contextmenu", (e) => e.preventDefault());
els.stage.addEventListener("pointerdown", (e) => {
  if (state !== "live") return;
  if (e.pointerType === "mouse") {
    els.stage.setPointerCapture(e.pointerId);
    send({ t: "mouse", act: "down", b: BTN[e.button] || "left", ...norm(e) });
    if (!els.kbd.matches(":focus")) els.stage.focus?.();
    return;
  }
  e.preventDefault();
  touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (touches.size === 1) {
    touchMode = "pending"; startPt = { ...norm(e), cx: e.clientX, cy: e.clientY };
    clearTimeout(longTimer);
    longTimer = setTimeout(() => {
      if (touchMode !== "pending") return;
      touchMode = "long";
      if (platform.toLowerCase() === "android") send({ t: "mouse", act: "long", x: startPt.x, y: startPt.y });
      else { send({ t: "mouse", act: "down", b: "right", x: startPt.x, y: startPt.y }); send({ t: "mouse", act: "up", b: "right", x: startPt.x, y: startPt.y }); }
      navigator.vibrate && navigator.vibrate(20);
    }, 550);
  } else if (touches.size === 2) {
    clearTimeout(longTimer);
    if (touchMode === "drag") send({ t: "mouse", act: "up", b: "left", x: startPt.x, y: startPt.y });
    touchMode = "scroll"; scrollAcc = 0; lastMid = mid();
  }
}, { passive: false });

function mid() { const p = [...touches.values()]; return { x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 }; }

els.stage.addEventListener("pointermove", (e) => {
  if (state !== "live") return;
  const now = performance.now();
  if (e.pointerType === "mouse") {
    if (now - lastMove < 25) return; lastMove = now;
    send({ t: "mouse", act: "move", ...norm(e) }); return;
  }
  if (!touches.has(e.pointerId)) return;
  touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (touchMode === "scroll" && touches.size >= 2) {
    const m = mid();
    if (zoom > 1) { panBy(m.x - lastMid.x, m.y - lastMid.y); lastMid = m; return; }
    scrollAcc += m.y - lastMid.y; lastMid = m;
    const r = els.canvas.getBoundingClientRect();
    while (Math.abs(scrollAcc) >= 30) {
      const dir = scrollAcc > 0 ? 1 : -1; scrollAcc -= dir * 30;
      send({ t: "mouse", act: "scroll", dy: dir, x: (m.x - r.left) / r.width, y: (m.y - r.top) / r.height });
    }
    return;
  }
  if (touchMode === "pending" && Math.hypot(e.clientX - startPt.cx, e.clientY - startPt.cy) > 10) {
    clearTimeout(longTimer); touchMode = "drag";
    send({ t: "mouse", act: "down", b: "left", x: startPt.x, y: startPt.y });
  }
  if (touchMode === "drag" && now - lastMove > 25) { lastMove = now; send({ t: "mouse", act: "move", ...norm(e) }); }
}, { passive: false });

function pointerEnd(e) {
  if (state !== "live") return;
  if (e.pointerType === "mouse") { send({ t: "mouse", act: "up", b: BTN[e.button] || "left", ...norm(e) }); return; }
  if (!touches.has(e.pointerId)) return;
  touches.delete(e.pointerId);
  clearTimeout(longTimer);
  if (touches.size > 0) return;
  if (touchMode === "pending") {
    send({ t: "mouse", act: "move", x: startPt.x, y: startPt.y });
    send({ t: "mouse", act: "down", b: "left", x: startPt.x, y: startPt.y });
    send({ t: "mouse", act: "up", b: "left", x: startPt.x, y: startPt.y });
  } else if (touchMode === "drag") send({ t: "mouse", act: "up", b: "left", ...norm(e) });
  touchMode = null;
}
els.stage.addEventListener("pointerup", pointerEnd);
els.stage.addEventListener("pointercancel", pointerEnd);
els.stage.addEventListener("wheel", (e) => {
  if (state !== "live") return; e.preventDefault();
  const dy = e.deltaY < 0 ? 1 : -1;
  const steps = Math.max(1, Math.min(5, Math.round(Math.abs(e.deltaY) / (e.deltaMode ? 1 : 100))));
  for (let i = 0; i < steps; i++) send({ t: "mouse", act: "scroll", dy, ...norm(e) });
}, { passive: false });

// ------------------------------------------------------------ keyboard
const isTyping = () => document.activeElement === els.kbd;
addEventListener("keydown", (e) => {
  if (state !== "live" || !els.chat.hidden && els.chat.contains(document.activeElement)) return;
  const mods = e.ctrlKey || e.metaKey || e.altKey;
  if (isTyping() && e.key.length === 1 && !mods) return;            // soft keyboard text goes through 'input'
  if (isTyping() && (e.key === "Unidentified" || e.key === "Process")) return;
  e.preventDefault();
  send({ t: "key", act: "down", key: e.key, code: e.code });
});
addEventListener("keyup", (e) => {
  if (state !== "live" || !els.chat.hidden && els.chat.contains(document.activeElement)) return;
  const mods = e.ctrlKey || e.metaKey || e.altKey;
  if (isTyping() && e.key.length === 1 && !mods) return;
  if (isTyping() && (e.key === "Unidentified" || e.key === "Process")) return;
  e.preventDefault();
  send({ t: "key", act: "up", key: e.key, code: e.code });
});
els.kbd.addEventListener("beforeinput", (e) => {
  if (state !== "live") return;
  if (e.inputType === "insertText" || e.inputType === "insertReplacementText") { if (e.data) send({ t: "text", s: e.data }); }
  else if (e.inputType === "deleteContentBackward") { send({ t: "key", act: "down", key: "Backspace" }); send({ t: "key", act: "up", key: "Backspace" }); }
  else if (e.inputType === "insertLineBreak" || e.inputType === "insertParagraph") { send({ t: "key", act: "down", key: "Enter" }); send({ t: "key", act: "up", key: "Enter" }); }
  else if (e.inputType === "insertCompositionText") return;
  e.preventDefault();
});
els.kbd.addEventListener("compositionend", (e) => { if (e.data) send({ t: "text", s: e.data }); els.kbd.value = ""; });
els.kbdBtn.onclick = () => {
  if (isTyping()) { els.kbd.blur(); els.kbdBtn.setAttribute("aria-pressed", "false"); }
  else { els.kbd.value = ""; els.kbd.focus(); els.kbdBtn.setAttribute("aria-pressed", "true"); }
};
els.kbd.addEventListener("blur", () => els.kbdBtn.setAttribute("aria-pressed", "false"));
els.navbar.addEventListener("click", (e) => { const a = e.target.closest("[data-nav]"); if (a) send({ t: "nav", a: a.dataset.nav }); });

$("fsBtn").onclick = () => {
  const el = document.documentElement;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else if (el.requestFullscreen) el.requestFullscreen().catch(() => toast("Full screen isn't available in this browser."));
  else toast("Full screen isn't available in this browser.");
};

// ------------------------------------------------------------ chat + files
function openChat() { els.chat.hidden = false; }
$("chatBtn").onclick = () => { openChat(); els.chatInput.focus(); };
$("chatClose").onclick = () => { els.chat.hidden = true; };
function addLog(cls, who, text) {
  const d = document.createElement("div"); d.className = cls;
  const b = document.createElement("b"); b.textContent = who + " ";
  d.append(b, document.createTextNode(text)); els.log.append(d); els.log.scrollTop = els.log.scrollHeight;
}
function addNote(text, node) {
  const d = document.createElement("div"); d.className = "note"; d.textContent = text + " ";
  if (node) d.append(node);
  els.log.append(d); els.log.scrollTop = els.log.scrollHeight;
}
els.chatForm.addEventListener("submit", (e) => {
  e.preventDefault(); const text = els.chatInput.value.trim(); if (!text) return;
  send({ t: "chat", text }); addLog("me", "You", text); els.chatInput.value = "";
});

$("fileBtn").onclick = () => els.filePick.click();
els.filePick.addEventListener("change", async () => {
  const f = els.filePick.files[0]; els.filePick.value = ""; if (!f || !conn) return;
  openChat(); addNote(`Sending ${f.name}…`);
  const fid = Math.random().toString(16).slice(2, 10);
  send({ t: "file_start", fid, name: f.name, size: f.size });
  const buf = new Uint8Array(await f.arrayBuffer());
  for (let i = 0; i < buf.length; i += FILE_CHUNK) {
    if (state !== "live") return;
    while (conn && conn.dataChannel && conn.dataChannel.bufferedAmount > 1 << 20) await new Promise((r) => setTimeout(r, 30));
    send({ t: "file_chunk", fid, data: b64encode(buf.subarray(i, i + FILE_CHUNK)) });
  }
  send({ t: "file_end", fid });
  addNote(`Sent ${f.name}.`);
});
function onFile(m) {
  if (m.t === "file_start") { incoming.set(m.fid, { name: m.name, parts: [] }); openChat(); addNote(`Receiving ${m.name}…`); }
  else if (m.t === "file_chunk" && incoming.has(m.fid)) incoming.get(m.fid).parts.push(b64decode(m.data));
  else if (m.t === "file_end" && incoming.has(m.fid)) {
    const f = incoming.get(m.fid); incoming.delete(m.fid);
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(f.parts)); a.download = f.name; a.textContent = "Save " + f.name;
    addNote("Received:", a);
  }
}

// ------------------------------------------------------------ start
(function init() {
  renderRecent();
  const m = location.hostname.match(/^([^.]+)\.github\.io$/);
  const repo = location.pathname.split("/").filter(Boolean)[0];
  $("dl").href = m && repo ? `https://github.com/${m[1]}/${repo}/releases/latest` : "https://github.com/";
  const pre = params.get("id"); if (pre) els.peerId.value = fmtId(pre);
  addEventListener("beforeunload", () => { if (state !== "idle") send({ t: "bye" }); });
})();
