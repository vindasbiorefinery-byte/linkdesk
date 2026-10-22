/* LinkDesk for Android — the phone being controlled.
   Same protocol as the desktop app; the native side (window.Android) captures
   the screen and performs taps, swipes and typing through Accessibility. */
"use strict";

const ID_PREFIX = "linkdesk-";
const ICE = [{ urls: "stun:stun.l.google.com:19302" }, { urls: "stun:global.stun.twilio.com:3478" }];
const CHUNK = 16000;
const A = window.Android;
const $ = (id) => document.getElementById(id);
const fmtId = (d) => d.replace(/(\d{3})(\d{3})(\d{3})/, "$1 $2 $3");

let myId = A.getPref("id");
if (!/^\d{9}$/.test(myId)) { myId = String(100000000 + Math.floor(Math.random() * 900000000)); A.setPref("id", myId); }
$("myId").textContent = fmtId(myId);

let peer = null, sess = null;

function status(text, cls) { const s = $("status"); s.textContent = "● " + text; s.className = "status " + cls; }
function msg(text) { $("msg").textContent = text || ""; }
function refreshControl() {
  const on = A.controlEnabled();
  $("ctl").textContent = on ? "● On — they can tap, swipe and type" : "● Off — view only";
  $("ctl").className = "status " + (on ? "ok" : "warn");
  $("ctlTip").hidden = on; $("ctlBtn").textContent = on ? "Accessibility settings" : "Turn on remote control";
}
refreshControl();
document.addEventListener("visibilitychange", refreshControl);
$("ctlBtn").onclick = () => A.openAccessibility();
$("copyBtn").onclick = () => { A.copyText(myId); msg("ID copied."); };

// ------------------------------------------------------------ signaling
function startPeer() {
  if (peer) try { peer.destroy(); } catch {}
  status("Connecting…", "warn");
  const opts = { config: { iceServers: ICE }, debug: 0 };
  const sig = A.getPref("signal");  // optional "host:port" for a self-hosted PeerJS server
  if (sig) { const [h, p] = sig.split(":"); Object.assign(opts, { host: h, port: Number(p || 443), path: "/", secure: A.getPref("signal_secure") !== "0", key: "peerjs" }); }
  peer = new Peer(ID_PREFIX + myId, opts);
  peer.on("open", () => status("Ready", "ok"));
  peer.on("connection", onConnection);
  peer.on("disconnected", () => { status("Reconnecting…", "warn"); setTimeout(() => { try { peer.reconnect(); } catch { startPeer(); } }, 2000); });
  peer.on("error", (e) => {
    const t = e && e.type;
    if (t === "unavailable-id") { status("ID still registered from a moment ago, retrying…", "warn"); setTimeout(startPeer, 8000); }
    else if (t === "network" || t === "server-error" || t === "socket-error" || t === "socket-closed") { status("Offline, retrying…", "bad"); setTimeout(startPeer, 5000); }
  });
}
startPeer();

// ------------------------------------------------------------ sessions
function onConnection(conn) {
  const s = { conn, state: "hello", who: (conn.metadata && conn.metadata.name) || "Web browser", acked: true, ackTimer: 0, gesture: null, lastSize: "", files: new Map() };
  const send = (o) => { if (conn.open) conn.send(JSON.stringify(o)); };
  s.send = send;
  conn.on("data", (data) => {
    if (typeof data !== "string") return;
    let m; try { m = JSON.parse(data); } catch { return; }
    if (s.state === "hello" && m.t === "hello") {
      s.who = String(m.name || s.who).slice(0, 40);
      if (sess && sess !== s) { send({ t: "denied", reason: "The partner is busy in another session." }); setTimeout(() => conn.close(), 300); return; }
      sess = s; s.state = "auth";
      send({ t: "challenge", nonce: Math.random().toString(16).slice(2), salt: "" });  // phones always ask the person
    } else if (s.state === "auth" && m.t === "auth") {
      s.state = "asking"; ask(s);
    } else if (s.state === "live") onLive(s, m);
  });
  conn.on("close", () => { if (sess === s) endSession("The connection was closed.", false); });
  conn.on("error", () => { if (sess === s) endSession("The connection was lost.", false); });
}

let askTimer = 0;
function ask(s) {
  $("askWho").textContent = s.who; $("ask").hidden = false;
  A.notifyRequest(s.who);
  const deadline = Date.now() + 30000;
  clearInterval(askTimer);
  askTimer = setInterval(() => {
    const left = Math.round((deadline - Date.now()) / 1000);
    if (left <= 0 || sess !== s) { answer(false); return; }
    $("askTimer").textContent = `Declines automatically in ${left}s`;
  }, 500);
}
function answer(ok) {
  clearInterval(askTimer); $("ask").hidden = true;
  const s = sess; if (!s || s.state !== "asking") return;
  if (!ok) { s.send({ t: "denied", reason: "The partner declined the request." }); sess = null; setTimeout(() => s.conn.close(), 300); return; }
  s.state = "starting";
  A.requestCapture();   // Android shows its own "Start sharing?" prompt; result in LD.onCapture
}
$("acceptBtn").onclick = () => answer(true);
$("declineBtn").onclick = () => answer(false);

window.LD = {
  onCapture(ok) {
    const s = sess; if (!s || s.state !== "starting") { if (ok) A.stopCapture(); return; }
    if (!ok) { s.send({ t: "denied", reason: "Screen sharing wasn't allowed on the phone." }); sess = null; setTimeout(() => s.conn.close(), 300); return; }
    const [w, h] = A.frameSize().split(",").map(Number);
    s.state = "live";
    s.send({ t: "accepted", platform: "Android", w, h, control: A.controlEnabled() });
    $("liveWho").textContent = s.who; $("liveCard").hidden = false; $("log").replaceChildren();
    msg(""); stream(s);
  },
  onCaptureStopped() { if (sess && sess.state === "live") endSession("Screen sharing was stopped on this phone.", true); },
};

async function stream(s) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let fid = 0;
  while (sess === s && s.state === "live") {
    if (s.acked) {
      const b64 = A.getFrame();
      if (b64) {
        const size = A.frameSize();
        if (size !== s.lastSize) { s.lastSize = size; const [w, h] = size.split(",").map(Number); s.send({ t: "screen", w, h }); }
        const bin = atob(b64); const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        fid = (fid + 1) >>> 0;
        const count = Math.max(1, Math.ceil(bytes.length / CHUNK));
        for (let i = 0; i < count; i++) {
          const part = bytes.subarray(i * CHUNK, (i + 1) * CHUNK);
          const msgb = new Uint8Array(9 + part.length); const dv = new DataView(msgb.buffer);
          msgb[0] = 0x46; dv.setUint32(1, fid); dv.setUint16(5, i); dv.setUint16(7, count); msgb.set(part, 9);
          if (s.conn.open) s.conn.send(msgb.buffer);
        }
        s.acked = false; clearTimeout(s.ackTimer); s.ackTimer = setTimeout(() => { s.acked = true; }, 2000);
      }
    }
    await sleep(70);
  }
}

function onLive(s, m) {
  switch (m.t) {
    case "ack": s.acked = true; clearTimeout(s.ackTimer); break;
    case "mouse": onMouse(s, m); break;
    case "key":
      if (m.act !== "down") break;
      if (m.key === "Backspace" || m.key === "Enter" || m.key === "Delete") A.key(m.key);
      else if (m.key && m.key.length === 1) A.typeText(m.key);
      else if (m.key === "Escape") A.nav("back");
      break;
    case "text": A.typeText(String(m.s || "").slice(0, 2000)); break;
    case "nav": A.nav(String(m.a)); break;
    case "chat": addLog("them", s.who, String(m.text || "").slice(0, 4000)); break;
    case "file_start": case "file_chunk": case "file_end": onFile(s, m); break;
    case "bye": endSession("The partner ended the session.", false); break;
  }
}

function onMouse(s, m) {
  const x = Math.min(Math.max(+m.x || 0, 0), 1), y = Math.min(Math.max(+m.y || 0, 0), 1), now = Date.now();
  if (m.act === "down" && m.b === "left") s.gesture = { pts: [[x, y]], t0: now, last: now };
  else if (m.act === "move" && s.gesture) { if (now - s.gesture.last > 30) { s.gesture.pts.push([x, y]); s.gesture.last = now; } }
  else if (m.act === "up" && m.b === "left" && s.gesture) {
    const g = s.gesture; s.gesture = null; g.pts.push([x, y]);
    const [x0, y0] = g.pts[0], dist = Math.hypot(x - x0, y - y0), dur = now - g.t0;
    if (dist < 0.015 && dur < 450) A.tap(x0, y0);
    else if (dist < 0.015) A.longPress(x0, y0);
    else A.swipe(JSON.stringify(g.pts.slice(-40)), Math.min(Math.max(dur, 80), 2500));
  }
  else if ((m.act === "down" && m.b === "right") || m.act === "long") A.longPress(x, y);
  else if (m.act === "scroll") A.scroll(x, y, +m.dy || 0);
}

function endSession(reason, tell) {
  const s = sess; sess = null; if (!s) return;
  if (tell) try { s.send({ t: "bye", reason: "The partner ended the session." }); } catch {}
  setTimeout(() => { try { s.conn.close(); } catch {} }, 300);
  A.stopCapture();
  $("liveCard").hidden = true; $("ask").hidden = true; clearInterval(askTimer);
  msg("Session ended. " + reason);
}
$("endBtn").onclick = () => endSession("You ended the session.", true);

// ------------------------------------------------------------ chat + files
function addLog(cls, who, text) {
  const d = document.createElement("div"); d.className = cls;
  if (who) { const b = document.createElement("b"); b.textContent = who + " "; d.append(b); }
  d.append(document.createTextNode(text)); $("log").append(d); $("log").scrollTop = 1e9;
}
$("chatForm").addEventListener("submit", (e) => {
  e.preventDefault(); const t = $("chatInput").value.trim(); if (!t || !sess) return;
  sess.send({ t: "chat", text: t }); addLog("me", "You", t); $("chatInput").value = "";
});
function onFile(s, m) {
  if (m.t === "file_start") { s.files.set(m.fid, A.fileStart(String(m.name || "file"))); addLog("note", "", `Receiving ${m.name}…`); }
  else if (m.t === "file_chunk" && s.files.has(m.fid)) A.fileChunk(s.files.get(m.fid), m.data);
  else if (m.t === "file_end" && s.files.has(m.fid)) {
    const where = A.fileEnd(s.files.get(m.fid)); s.files.delete(m.fid);
    addLog("note", "", `Saved to ${where}`); s.send({ t: "note", text: "The file was saved on the phone." });
  }
}
$("fileBtn").onclick = () => $("filePick").click();
$("filePick").addEventListener("change", async () => {
  const f = $("filePick").files[0]; $("filePick").value = ""; const s = sess; if (!f || !s) return;
  const fid = Math.random().toString(16).slice(2, 10);
  s.send({ t: "file_start", fid, name: f.name, size: f.size });
  const buf = new Uint8Array(await f.arrayBuffer());
  for (let i = 0; i < buf.length; i += 12288) {
    if (sess !== s) return;
    while (s.conn.dataChannel && s.conn.dataChannel.bufferedAmount > 1 << 20) await new Promise((r) => setTimeout(r, 30));
    let str = ""; const part = buf.subarray(i, i + 12288);
    for (let j = 0; j < part.length; j += 0x8000) str += String.fromCharCode.apply(null, part.subarray(j, j + 0x8000));
    s.send({ t: "file_chunk", fid, data: btoa(str) });
  }
  s.send({ t: "file_end", fid }); addLog("note", "", `Sent ${f.name}.`);
});
