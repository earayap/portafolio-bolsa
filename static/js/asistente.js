/* Asistente del portafolio — chat contra un modelo local (Ollama) */

const state = {
  history: [], // formato Ollama /api/chat, sin el system prompt (lo agrega el backend)
};

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || String(r.status));
  return data;
}

function addMessage(text, who) {
  const log = document.getElementById("chatLog");
  const wrap = document.createElement("div");
  wrap.className = `chat-msg chat-msg-${who}`;
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

async function enviar(mensaje) {
  const errorEl = document.getElementById("chatError");
  const sendBtn = document.getElementById("chatSend");
  errorEl.textContent = "";
  addMessage(mensaje, "user");

  const pensando = addMessage("Pensando…", "bot");
  pensando.classList.add("chat-bubble-pending");
  sendBtn.disabled = true;

  try {
    const r = await postJSON("/api/asistente/chat", { message: mensaje, history: state.history });
    state.history = r.history || state.history;
    pensando.textContent = r.reply || "(sin respuesta)";
    pensando.classList.remove("chat-bubble-pending");
  } catch (e) {
    pensando.remove();
    errorEl.textContent = "Error: " + e.message;
  } finally {
    sendBtn.disabled = false;
  }
}

function bindUI() {
  document.getElementById("chatForm").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const input = document.getElementById("chatInput");
    const mensaje = input.value.trim();
    if (!mensaje) return;
    input.value = "";
    enviar(mensaje);
  });
  document.getElementById("themeBtn").addEventListener("click", () => {
    const el = document.documentElement;
    const light = el.getAttribute("data-theme") === "light";
    el.setAttribute("data-theme", light ? "dark" : "light");
    document.getElementById("themeBtn").textContent = light ? "☀️" : "🌙";
  });
}

function clock() {
  const now = new Date().toLocaleString("es-CL", {
    timeZone: "America/Santiago", hour: "2-digit", minute: "2-digit", second: "2-digit",
    day: "2-digit", month: "short",
  });
  document.getElementById("clock").textContent = "🇨🇱 " + now;
}

clock();
setInterval(clock, 1000);
bindUI();
document.getElementById("chatInput").focus();
