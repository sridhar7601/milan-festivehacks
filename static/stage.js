// Stage view: one lane per language, each with its own WebSocket listener, so the
// backend translates into exactly the languages on screen. Choose with ?langs=ta-IN,pa-IN
const room = decodeURIComponent(location.pathname.split("/").pop());
const MAX_LANES = 6;
document.getElementById("room").textContent = room;
const joinUrl = `${location.origin}/r/${encodeURIComponent(room)}`;
if (window.QRCode) new QRCode(document.getElementById("stageQr"), { text: joinUrl, width: 120, height: 120 });
else document.querySelector(".joinqr").hidden = true;

const cfg = await fetch("/api/config").then((r) => r.json());
const requested = (new URLSearchParams(location.search).get("langs") || "").split(",").filter((c) => cfg.languages[c]);
const langs = (requested.length ? requested : Object.keys(cfg.languages)).slice(0, MAX_LANES);

const stage = document.getElementById("stage");
for (const lang of langs) {
  const lane = document.createElement("section");
  lane.className = "lane";
  lane.innerHTML = `<h2></h2><div class="who"></div><div class="line muted">…</div>`;
  lane.querySelector("h2").textContent = cfg.languages[lang];
  stage.append(lane);
  listen(lang, lane, 0);
}

function listen(lang, lane, retry) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/${room}?lang=${encodeURIComponent(lang)}&name=stage`);
  ws.onopen = () => { retry = 0; };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "flags" || msg.type === "welcome") document.getElementById("rushPill").style.display = msg.rush_mode ? "inline-flex" : "none";
    if (msg.type !== "caption") return;
    const line = document.createElement("div");
    line.className = "line fresh";
    line.textContent = msg.text;
    lane.querySelector(".who").textContent = msg.speaker;
    // keep the previous caption, dimmed, above the newest one which fades in
    lane.querySelector(".prev")?.remove();
    const old = lane.querySelector(".line");
    if (old && !old.classList.contains("muted")) { old.className = "prev"; }
    else old?.remove();
    lane.append(line);
  };
  ws.onclose = () => setTimeout(() => listen(lang, lane, retry + 1), Math.min(1000 * 2 ** retry, 10000));
}
