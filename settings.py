"""Admin "Camera settings" page: enter camera IP / login once, saved in MongoDB (no .env editing)."""
import re
import logging
from datetime import datetime
import cv2
from flask import Blueprint, jsonify, request, Response
from config import Config
from database import Database
from app.auth import login_required
from services.cctv_service import CCTVService

logger = logging.getLogger(__name__)
settings_bp = Blueprint("settings", __name__)

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$")
PATH_RE = re.compile(r"^[A-Za-z0-9_\-./?=&%:+]{0,200}$")


def _fail(message, code=400):
    return jsonify({"success": False, "message": message}), code


def load_saved_camera_settings():
    """Called once at startup: settings saved from the admin page win over .env."""
    try:
        db = Database.get_db()
        doc = db.settings.find_one({"_id": "cctv"}) if db is not None else None
        if doc:
            Config.apply_cctv_settings(doc)
            logger.info("Camera settings loaded from the database.")
    except Exception as e:
        logger.warning(f"Could not load saved camera settings: {e}")


def _validate(d):
    ip = (d.get("ip") or "").strip()
    username = (d.get("username") or "").strip()
    password = d.get("password")
    stream_path = (d.get("stream_path") or "").strip().lstrip("/")
    try:
        port = int(d.get("port") or 554)
    except (TypeError, ValueError):
        return None, "Port must be a number."
    if not HOST_RE.match(ip):
        return None, "Enter a valid camera IP address or host name."
    if not 1 <= port <= 65535:
        return None, "Port must be between 1 and 65535."
    if len(username) > 64:
        return None, "Username is too long."
    if password is not None and len(password) > 128:
        return None, "Password is too long."
    if not PATH_RE.match(stream_path):
        return None, "Stream path has invalid characters."
    if not password:            # blank = keep the password already saved
        password = Config.CCTV_PASSWORD
    return {"ip": ip, "username": username, "password": password,
            "port": port, "stream_path": stream_path}, None


@settings_bp.get("/api/settings/camera")
@login_required("admin")
def get_camera_settings():
    return jsonify({
        "ip": Config.CCTV_IP, "username": Config.CCTV_USERNAME,
        "port": Config.CCTV_RTSP_PORT, "stream_path": Config.CCTV_STREAM_PATH,
        "has_password": bool(Config.CCTV_PASSWORD),          # the password itself is never sent back
        "masked_url": Config.get_masked_cctv_url(),
        "status": CCTVService().get_status(),
    })


@settings_bp.post("/api/settings/camera")
@login_required("admin")
def save_camera_settings():
    clean, err = _validate(request.get_json(silent=True) or {})
    if err:
        return _fail(err)
    db = Database.get_db()
    if db is None:
        return _fail("Database not connected.", 503)
    db.settings.update_one(
        {"_id": "cctv"},
        {"$set": {**clean, "updated_at": datetime.now().isoformat(timespec="seconds")}},
        upsert=True,
    )
    Config.apply_cctv_settings(clean)
    CCTVService().reconnect()
    return jsonify({"success": True, "message": "Camera settings saved. Reconnecting to the camera…"})


@settings_bp.post("/api/settings/camera/test")
@login_required("admin")
def test_camera_settings():
    clean, err = _validate(request.get_json(silent=True) or {})
    if err:
        return _fail(err)
    url = Config.build_rtsp_url(clean["ip"], clean["username"], clean["password"],
                                clean["port"], clean["stream_path"])
    cap = None
    try:
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                               [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 6000,
                                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 6000])
        if not cap.isOpened():
            return jsonify({"success": False, "message":
                            "Could not open the stream. Check the IP, username, password and stream path."})
        good, frame = cap.read()
        if not good or frame is None:
            return jsonify({"success": False, "message":
                            "The camera answered but sent no video. Try a different stream path."})
        h, w = frame.shape[:2]
        return jsonify({"success": True, "message": f"Connection works ({w}x{h}). Click Save to use it."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Test failed: {e}"})
    finally:
        if cap is not None:
            cap.release()


@settings_bp.get("/admin/camera")
@login_required("admin")
def camera_settings_page():
    return Response(PAGE, mimetype="text/html")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Camera settings · Smart Attendance System</title>
<style>
  :root{--bg:#f3f4f6;--card:#fff;--text:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb;--good:#16a34a;--bad:#dc2626}
  *{box-sizing:border-box}
  [hidden]{display:none!important}
  body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:640px;margin:32px auto;padding:0 16px}
  .card{background:var(--card);border-radius:14px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  h1{margin:0 0 4px;font-size:22px} p.sub{margin:0 0 20px;color:var(--muted)}
  .status{display:flex;gap:8px;align-items:center;margin-bottom:18px;padding:10px 12px;border:1px solid var(--line);border-radius:10px}
  .dot{width:10px;height:10px;border-radius:50%;background:var(--muted)}
  .dot.good{background:var(--good)} .dot.bad{background:var(--bad)}
  label{display:block;margin-bottom:14px;font-weight:600;font-size:14px}
  input{display:block;width:100%;margin-top:6px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;font:inherit}
  input:focus{outline:2px solid var(--accent);border-color:transparent}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  small{display:block;margin-top:4px;font-weight:400;color:var(--muted)}
  .btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
  button{padding:10px 18px;border:0;border-radius:8px;font:inherit;font-weight:600;cursor:pointer}
  button:disabled{opacity:.6;cursor:wait}
  .primary{background:var(--accent);color:#fff} .outline{background:#eff6ff;color:var(--accent)}
  .alert{margin-top:16px;padding:10px 12px;border-radius:8px}
  .alert.success{background:#dcfce7;color:#166534} .alert.error{background:#fee2e2;color:#991b1b}
  a.back{display:inline-block;margin-bottom:12px;color:var(--accent);text-decoration:none}
</style></head>
<body><div class="wrap">
  <a class="back" href="/">&larr; Back</a>
  <div class="card">
    <h1>Camera settings</h1>
    <p class="sub">Enter your CCTV camera details. They are saved securely in the database, so there is no .env file to edit.</p>
    <div class="status"><span id="dot" class="dot"></span><span id="statusText">Checking…</span></div>

    <label>Camera IP address
      <input id="ip" placeholder="e.g. 192.168.0.107" autocomplete="off">
    </label>
    <div class="row">
      <label>Username<input id="username" placeholder="admin" autocomplete="off"></label>
      <label>Password<input id="password" type="password" placeholder="Camera password" autocomplete="new-password"></label>
    </div>
    <div class="row">
      <label>RTSP port<input id="port" type="number" value="554" min="1" max="65535"></label>
      <label>Stream path<input id="path" list="paths" placeholder="h264Preview_01_sub" autocomplete="off"></label>
    </div>
    <datalist id="paths">
      <option value="h264Preview_01_sub"><option value="h264Preview_01_main">
      <option value="stream1"><option value="stream2">
      <option value="cam/realmonitor?channel=1&amp;subtype=1"><option value="Streaming/Channels/102">
      <option value="live/ch00_1"><option value="video1">
    </datalist>
    <small style="margin:-6px 0 16px">Leave the password blank to keep the one already saved. Most cameras use the SUB stream for smooth video.</small>

    <div class="btns">
      <button id="testBtn" class="outline" type="button">Test connection</button>
      <button id="saveBtn" class="primary" type="button">Save &amp; connect</button>
    </div>
    <div id="msg" class="alert" hidden></div>
  </div>
</div>
<script>
  const $ = (id) => document.getElementById(id);
  const fields = () => ({ ip: $("ip").value.trim(), username: $("username").value.trim(),
    password: $("password").value, port: $("port").value, stream_path: $("path").value.trim() });

  function show(text, kind) { const m = $("msg"); m.textContent = text; m.className = "alert " + kind; m.hidden = false; }

  async function load(fillForm) {
    try {
      const r = await fetch("/api/settings/camera");
      if (!r.ok) return;
      const d = await r.json();
      if (fillForm) {
        $("ip").value = d.ip || ""; $("username").value = d.username || "";
        $("port").value = d.port || 554; $("path").value = d.stream_path || "";
        $("password").placeholder = d.has_password ? "Saved \\u2013 leave blank to keep it" : "Camera password";
      }
      const ok = d.status && d.status.connected;
      $("dot").className = "dot " + (ok ? "good" : "bad");
      $("statusText").textContent = ok ? "Camera connected" :
        "Camera disconnected" + (d.status && d.status.error ? " \\u2013 " + d.status.error : "");
    } catch (e) { $("statusText").textContent = "Could not read status"; }
  }

  async function send(url, btn, busyText, idleText) {
    btn.disabled = true; btn.textContent = busyText;
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(fields()) });
      const d = await r.json().catch(() => ({}));
      show(d.message || (r.ok ? "Done." : "Something went wrong."), (r.ok && d.success) ? "success" : "error");
      return d;
    } finally { btn.disabled = false; btn.textContent = idleText; }
  }

  $("testBtn").onclick = () => send("/api/settings/camera/test", $("testBtn"), "Testing…", "Test connection");
  $("saveBtn").onclick = async () => {
    const d = await send("/api/settings/camera", $("saveBtn"), "Saving…", "Save & connect");
    if (d && d.success) { $("password").value = ""; setTimeout(() => load(true), 3000); }
  };

  load(true);
  setInterval(() => load(false), 4000);
</script></body></html>
"""