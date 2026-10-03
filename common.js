// Shared helpers
const $ = (id) => document.getElementById(id);

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(url, opts = {}) {
  try {
    const r = await fetch(url, opts);
    if (r.status === 401) { location.href = "/login"; return { ok: false, status: 401, data: {} }; }
    let data = {};
    try { data = await r.json(); } catch (_) {}
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: { message: "Cannot reach the server." } };
  }
}

function postJSON(url, body) {
  return api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
}

function showMsg(el, text, kind) {
  el.textContent = text;
  el.className = "alert " + (kind || "");
  el.hidden = false;
}
