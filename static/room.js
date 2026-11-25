// Milan room client: WebSocket captions + voice, push-to-talk WAV capture.
import { WavRecorder } from "/static/recorder.js";

const room = decodeURIComponent(location.pathname.split("/").pop());
const $ = (id) => document.getElementById(id);
const store = {
  get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const guestId = store.get("milan.guest", null) || crypto.randomUUID();
store.set("milan.guest", guestId);
$("roomName").textContent = room;
$("stageLink").href = `/stage/${encodeURIComponent(room)}`;
$("name").value = store.get("milan.name", "");

let langNames = {};
const short = (code) => (code && code !== "auto" ? code.split("-")[0].toUpperCase() : "AUTO");

// ---- toast (replaces alert) ----
let toastTimer;
function toast(msg, kind = "info") {
  const t = $("toast");
  t.textContent = msg;
  t.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 3500);
}

// ---- share: QR + copy link ----
const roomUrl = `${location.origin}/r/${encodeURIComponent(room)}`;
$("roomUrl").textContent = roomUrl;
if (window.QRCode) new QRCode($("roomQr"), { text: roomUrl, width: 140, height: 140 });
else $("roomQr").hidden = true;
$("copyBtn").onclick = async () => {
  try { await navigator.clipboard.writeText(roomUrl); toast("Link copied. Share it with your guests!"); }
  catch { window.getSelection().selectAllChildren($("roomUrl")); toast("Press Ctrl/⌘+C to copy the link"); }
};

let ws, voiceOn = false, retry = 0, seen = new Set();
const audioQueue = [];
let playing = false;

async function loadConfig() {
  const cfg = await fetch("/api/config").then((r) => r.json());
  langNames = cfg.languages;
  const sel = $("lang");
  sel.innerHTML = "";
  for (const [code, label] of Object.entries(cfg.languages)) sel.add(new Option(label, code));
  const saved = store.get("milan.lang", null);
  sel.value = saved && cfg.languages[saved] ? saved : (navigator.language?.startsWith("hi") ? "hi-IN" : "en-IN");
  if (!sel.value) sel.selectedIndex = 0;
  applyFlags(cfg.flags);
}

function applyFlags(f) {
  $("rush").classList.toggle("show", !!f.rush_mode);
  if (typeof f.elder_mode === "boolean" && f.elder_mode) { $("elder").checked = true; toggleElder(); }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const qs = new URLSearchParams({
    lang: $("lang").value, guest: guestId, name: $("name").value || "Guest",
    role: $("elder").checked ? "elder" : "guest",
  });
  ws = new WebSocket(`${proto}://${location.host}/ws/${room}?${qs}`);
  ws.onopen = () => { retry = 0; setStatus("live", true); };
  ws.onclose = () => {
    setStatus("reconnecting…", false);
    setTimeout(connect, Math.min(1000 * 2 ** retry++, 10000));
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

function setStatus(text, live) { const s = $("status"); s.textContent = text; s.classList.toggle("live", live); }

function handle(msg) {
  switch (msg.type) {
    case "welcome": applyFlags(msg); break;
    case "flags": {
      applyFlags(msg);
      const current = [...$("lang").options].map((o) => o.value).join(",");
      if (msg.enabled_languages && msg.enabled_languages.join(",") !== current) loadConfig();
      break;
    }
    case "presence": $("langs").textContent = `${msg.languages.length} language${msg.languages.length === 1 ? "" : "s"} live`; break;
    case "caption": addCaption(msg); break;
  }
}

function addCaption(c) {
  if (seen.has(c.id)) return;
  seen.add(c.id);
  $("empty")?.remove();
  const el = document.createElement("article");
  el.className = "cap" + (c.translated ? "" : " untranslated");
  const who = document.createElement("div"); who.className = "who";
  const name = document.createElement("span"); name.textContent = c.speaker;
  const mine = $("lang").value;
  const tag = document.createElement("span"); tag.className = "ltag";
  const src = c.source_lang && c.source_lang !== "auto" ? short(c.source_lang) : "";
  tag.textContent = c.translated && c.source_lang !== mine ? `${src ? src + " " : ""}→ ${short(mine)}` : (src || short(mine));
  tag.title = c.translated ? `Translated${src ? ` from ${langNames[c.source_lang] || c.source_lang}` : ""} to ${langNames[mine] || mine}` : "Original language";
  const play = document.createElement("span"); play.className = "play-ind"; play.textContent = "🔊 playing";
  const when = document.createElement("span"); when.className = "when";
  when.textContent = new Date((c.ts || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  who.append(name, tag, play, when);
  const av = document.createElement("div"); av.className = "avatar"; av.setAttribute("aria-hidden", "true");
  av.textContent = ([...(c.speaker || "?").trim()][0] || "?").toUpperCase();
  const AV = ["var(--marigold)", "var(--sindoor)", "var(--leaf)", "#ffd889", "#ff8f3a", "#7fd8b4"];
  av.style.setProperty("--av", AV[[...(c.speaker || "")].reduce((h, ch) => (h * 31 + ch.codePointAt(0)) >>> 0, 7) % AV.length]);
  el.append(av);
  const txt = document.createElement("div"); txt.className = "txt"; txt.textContent = c.text;
  el.append(who, txt);
  if (c.original && c.original !== c.text) {
    const o = document.createElement("div"); o.className = "orig"; o.textContent = `“${c.original}”`; el.append(o);
  }
  $("feed").prepend(el);
  while ($("feed").children.length > 50) $("feed").lastChild.remove();
  if (voiceOn && c.audio) { audioQueue.push({ audio: c.audio, el }); playNext(); }
}

function playNext() {
  if (playing || !audioQueue.length) return;
  playing = true;
  const { audio, el } = audioQueue.shift();
  const a = new Audio(`data:audio/wav;base64,${audio}`);
  const done = () => { el.classList.remove("playing"); playing = false; playNext(); };
  a.onended = a.onerror = done;
  el.classList.add("playing");
  a.play().catch(done);
}

function toggleElder() { document.body.classList.toggle("elder", $("elder").checked); }

// ---- controls ----
$("lang").onchange = () => {
  store.set("milan.lang", $("lang").value);
  if (ws?.readyState === 1) ws.send(JSON.stringify({ type: "set_lang", lang: $("lang").value }));
};
$("name").onchange = () => store.set("milan.name", $("name").value);
$("elder").onchange = toggleElder;
$("voiceBtn").onclick = () => {
  voiceOn = !voiceOn;
  $("voiceBtn").textContent = voiceOn ? "🔊 Voice on" : "🔈 Enable voice";
  if (voiceOn) new Audio("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=").play().catch(() => {});
};
$("projBtn").onclick = () => document.body.classList.toggle("projector");
document.addEventListener("keydown", (e) => { if (e.key === "Escape") document.body.classList.remove("projector"); });

async function post(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Error ${r.status}`);
  return r.json();
}

$("send").onclick = async () => {
  const text = $("text").value.trim();
  if (!text) return;
  const btn = $("send");
  btn.disabled = true; btn.classList.add("busy"); btn.textContent = "Translating…"; btn.setAttribute("aria-busy", "true");
  try {
    await post(`/api/rooms/${room}/say`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, speaker: $("name").value || "Guest" }),
    });
    $("text").value = "";
  } catch (e) { toast(e.message, "error"); }
  finally { btn.disabled = false; btn.classList.remove("busy"); btn.textContent = "Send"; btn.removeAttribute("aria-busy"); }
};
$("text").addEventListener("keydown", (e) => { if (e.key === "Enter") $("send").click(); });

// Push-to-talk: hold, or tap to toggle.
const rec = new WavRecorder();
let recording = false, pressStart = 0;
async function startRec() {
  if (recording) return;
  try { await rec.start(); recording = true; $("talk").classList.add("rec"); $("talk").setAttribute("aria-pressed", "true"); }
  catch { toast("Microphone permission is needed to speak.", "error"); }
}
async function stopRec() {
  if (!recording) return;
  recording = false; $("talk").classList.remove("rec"); $("talk").setAttribute("aria-pressed", "false");
  const blob = await rec.stop();
  if (blob.size < 2000) { toast("That was too short. Hold 🎙 while you speak."); return; } // too short to contain speech
  const fd = new FormData();
  fd.append("audio", blob, "speech.wav");
  fd.append("speaker", $("name").value || "Guest");
  const talk = $("talk");
  talk.disabled = true; talk.classList.add("busy"); talk.textContent = "⏳"; talk.setAttribute("aria-busy", "true");
  try { await post(`/api/rooms/${room}/speak`, { method: "POST", body: fd }); }
  catch (e) { toast(e.message, "error"); }
  finally { talk.disabled = false; talk.classList.remove("busy"); talk.textContent = "🎙"; talk.removeAttribute("aria-busy"); }
}
$("talk").addEventListener("pointerdown", () => { pressStart = Date.now(); recording ? stopRec() : startRec(); });
$("talk").addEventListener("pointerup", () => { if (recording && Date.now() - pressStart > 400) stopRec(); });

await loadConfig();
connect();

// ---- first-time walkthrough (shown once per browser, replay with "?") ----
const TOUR = [
  { sel: "#lang", text: "Pick the language you want to hear" },
  { sel: "#voiceBtn", text: "Tap Enable voice to hear it spoken" },
  { sel: ".dock", text: "Hold 🎙 to speak, or type below" },
];
let tourStep = -1, tourReturn = null;
function showTour() {
  tourReturn = document.activeElement;
  tourStep = 0;
  $("tour").hidden = false; $("tourBox").hidden = false;
  renderTour();
}
function renderTour() {
  document.querySelector(".tour-hl")?.classList.remove("tour-hl");
  const step = TOUR[tourStep];
  const target = document.querySelector(step.sel);
  target.classList.add("tour-hl");
  if (getComputedStyle(target).position !== "fixed") target.scrollIntoView({ block: "center" });
  $("tourCount").textContent = `Step ${tourStep + 1} of ${TOUR.length}`;
  $("tourText").textContent = step.text;
  $("tourNext").textContent = tourStep === TOUR.length - 1 ? "Got it" : "Next";
  [...$("tourDots").children].forEach((d, i) => d.classList.toggle("on", i === tourStep));
  placeTourBox();
  $("tourNext").focus();
}
function placeTourBox() {
  if (tourStep < 0) return;
  const r = document.querySelector(TOUR[tourStep].sel).getBoundingClientRect();
  const box = $("tourBox"), bh = box.offsetHeight, bw = box.offsetWidth, gap = 14;
  const below = r.bottom + gap + bh < innerHeight;
  box.style.top = `${below ? r.bottom + gap : Math.max(12, r.top - bh - gap)}px`;
  box.style.left = `${Math.min(Math.max(16, r.left + r.width / 2 - bw / 2), innerWidth - bw - 16)}px`;
}
function endTour() {
  if (tourStep < 0) return;
  tourStep = -1;
  document.querySelector(".tour-hl")?.classList.remove("tour-hl");
  $("tour").hidden = true; $("tourBox").hidden = true;
  store.set("milan.onboarded", "1");
  (tourReturn && tourReturn !== document.body ? tourReturn : $("helpBtn")).focus();
}
$("tourNext").onclick = () => { if (tourStep >= TOUR.length - 1) endTour(); else { tourStep++; renderTour(); } };
$("tourSkip").onclick = endTour;
$("tour").onclick = endTour;
$("helpBtn").onclick = showTour;
addEventListener("resize", placeTourBox);
document.addEventListener("keydown", (e) => {
  if (tourStep < 0) return;
  if (e.key === "Escape") { e.preventDefault(); endTour(); }
  else if (e.key === "Tab") { // keep focus inside the dialog
    const f = [$("tourSkip"), $("tourNext")];
    const i = f.indexOf(document.activeElement);
    e.preventDefault();
    f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
  }
});
if (!store.get("milan.onboarded", null)) showTour();
