import os
import re
import time
import shutil
import logging
from datetime import datetime
import cv2
import numpy as np
from flask import Blueprint, jsonify, request, session, Response, send_file, abort
from pymongo.errors import DuplicateKeyError
from werkzeug.security import generate_password_hash
from config import Config
from database import Database
from app.auth import login_required
from services.cctv_service import CCTVService
from services.recognition_service import RecognitionService
from services.session_worker import SessionWorker
from services.excel_service import ExcelService

logger = logging.getLogger(__name__)
api = Blueprint("api", __name__, url_prefix="/api")

STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def ok(**kw):
    return jsonify({"success": True, **kw})


def fail(message, code=400):
    return jsonify({"success": False, "message": message}), code


def get_db():
    db = Database.get_db()
    if db is None:
        abort(503, "Database not connected.")
    return db


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# =========================================================== CCTV
@api.get("/cctv/status")
@login_required("teacher", "admin")
def cctv_status():
    return jsonify(CCTVService().get_status())  # returns instantly, never touches the network


def _placeholder(text):
    img = np.zeros((360, 640, 3), np.uint8)
    cv2.putText(img, text, (150, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
    return img


def _draw_overlay(frame, results):
    h, w = frame.shape[:2]
    thick = max(2, w // 500)
    scale = max(0.5, w / 1600.0)
    for r in results:
        x, y, bw, bh = r["box"]
        color = (0, 170, 0) if r["recognized"] else (0, 0, 220)
        cv2.rectangle(frame, (x, y), (x + bw, y + bh), color, thick)
        label = r["name"] if r["recognized"] else "Unknown"
        cv2.putText(frame, label, (x, max(15, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick)


def _mjpeg():
    cctv, worker = CCTVService(), SessionWorker()
    while True:
        if cctv.is_connected():
            frame = cctv.read_frame()
            delay = 0.1  # ~10 fps is plenty for a preview
            if frame is None:
                continue
            _draw_overlay(frame, worker.get_overlay())
        else:
            frame = _placeholder("Camera disconnected")
            delay = 0.5
        if frame.shape[1] > 960:
            scale = 960.0 / frame.shape[1]
            frame = cv2.resize(frame, (960, int(frame.shape[0] * scale)))
        good, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if good:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
        time.sleep(delay)


@api.get("/cctv/video_feed")
@login_required("teacher", "admin")
def video_feed():
    resp = Response(_mjpeg(), mimetype="multipart/x-mixed-replace; boundary=frame")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# =========================================================== Sessions
@api.post("/session/start")
@login_required("teacher", "admin")
def session_start():
    subject = ((request.get_json(silent=True) or {}).get("subject") or "").strip()
    if not subject or len(subject) > 60:
        return fail("Enter a subject name (up to 60 characters).")
    user = session["user"]
    started, result = SessionWorker().start_session(subject, user["name"], user["username"])
    return ok(session=result) if started else fail(result, 409)


@api.post("/session/end")
@login_required("teacher", "admin")
def session_end():
    ended, message = SessionWorker().end_session()
    return ok(message=message) if ended else fail(message, 409)


@api.get("/session/status")
@login_required("teacher", "admin")
def session_status():
    return jsonify(SessionWorker().get_status())


@api.get("/sessions")
@login_required("teacher", "admin")
def list_sessions():
    rows = list(get_db().sessions.find({}, {"_id": 0}).sort("created_at", -1).limit(50))
    return jsonify(rows)


@api.get("/stats")
@login_required("teacher", "admin")
def stats():
    db = get_db()
    return jsonify({
        "total_enrolled": db.students.count_documents({"face_encodings.0": {"$exists": True}}),
        "total_teachers": db.users.count_documents({"role": "teacher"}),
        "total_sessions": db.sessions.count_documents({}),
    })


# =========================================================== Students
@api.get("/students")
@login_required("teacher", "admin")
def list_students():
    rows = list(get_db().students.find({}, {"_id": 0, "face_encodings": 0}).sort("student_id", 1))
    return jsonify(rows)


@api.post("/students/register")
@login_required("teacher", "admin")
def register_student():
    db = get_db()
    f = request.form
    sid = (f.get("student_id") or "").strip()
    name = (f.get("name") or "").strip()
    if not STUDENT_ID_RE.match(sid):
        return fail("Student ID must be 2-32 characters: letters, numbers, - or _.")
    if not name:
        return fail("Name is required.")

    try:
        rec = RecognitionService.get()
    except Exception as e:
        return fail(f"Face models are not ready: {e}", 503)

    existing = db.users.find_one({"username": sid})
    if existing and existing.get("role") != "student":
        return fail("That ID is already used by a staff account.", 409)

    images = []
    for file in request.files.getlist("photos"):
        arr = np.frombuffer(file.read(), np.uint8)
        images.append((file.filename or "photo", cv2.imdecode(arr, cv2.IMREAD_COLOR)))
    if f.get("use_cctv") == "1":
        cctv = CCTVService()
        images.append(("CCTV capture", cctv.read_frame() if cctv.is_connected() else None))
    if not images:
        return fail("Upload at least one photo or tick 'Capture from CCTV'.")

    encodings, good_images, errors = [], [], []
    for label, img in images:
        enc, msg = rec.extract_single_encoding(img)
        if enc is None:
            errors.append(f"{label}: {msg}")
        else:
            encodings.append(enc)
            good_images.append(img)
    if not encodings:
        return fail("No usable photo. " + "; ".join(errors))

    folder = os.path.join(Config.UPLOADS_DIR, "students", sid)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    for i, img in enumerate(good_images, 1):
        cv2.imwrite(os.path.join(folder, f"{i}.jpg"), img)

    profile = {
        "student_id": sid, "name": name,
        "email": (f.get("email") or "").strip(),
        "department": (f.get("department") or "").strip(),
        "year": (f.get("year") or "").strip(),
        "division": (f.get("division") or "").strip(),
        "roll_number": (f.get("roll_number") or "").strip(),
        "face_encodings": encodings, "photo_count": len(encodings), "updated_at": now_iso(),
    }
    db.students.update_one({"student_id": sid}, {"$set": profile, "$setOnInsert": {"created_at": now_iso()}}, upsert=True)

    # Login account for the student. Default password = student ID unless one was given.
    given = f.get("password") or ""
    user_set = {"name": name, "role": "student", "student_id": sid}
    on_insert = {"created_at": now_iso()}
    if given:
        user_set["password_hash"] = generate_password_hash(given)
    else:
        on_insert["password_hash"] = generate_password_hash(sid)
    try:
        db.users.update_one({"username": sid}, {"$set": user_set, "$setOnInsert": on_insert}, upsert=True)
    except DuplicateKeyError:
        return fail("A user with that ID already exists.", 409)

    SessionWorker().reload_gallery()
    return ok(message=f"{name} saved with {len(encodings)} photo(s).", rejected=errors)


@api.delete("/students/<student_id>")
@login_required("teacher", "admin")
def delete_student(student_id):
    db = get_db()
    res = db.students.delete_one({"student_id": student_id})
    db.users.delete_one({"username": student_id, "role": "student"})
    shutil.rmtree(os.path.join(Config.UPLOADS_DIR, "students", student_id), ignore_errors=True)
    SessionWorker().reload_gallery()
    return ok(deleted=res.deleted_count)


# =========================================================== Teachers (admin only)
@api.get("/teachers")
@login_required("admin")
def list_teachers():
    rows = list(get_db().users.find({"role": "teacher"}, {"_id": 0, "password_hash": 0, "password": 0}))
    return jsonify(rows)


@api.post("/teachers/add")
@login_required("admin")
def add_teacher():
    d = request.get_json(silent=True) or {}
    username = (d.get("username") or "").strip()
    name = (d.get("name") or "").strip()
    password = d.get("password") or ""
    email = (d.get("email") or "").strip()
    if not USERNAME_RE.match(username):
        return fail("Username must be 3-32 characters: letters, numbers, . - or _.")
    if not name:
        return fail("Name is required.")
    if len(password) < 6:
        return fail("Password must be at least 6 characters.")

    doc = {"username": username, "name": name, "role": "teacher",
           "password_hash": generate_password_hash(password), "created_at": now_iso()}
    if email:                      # never store an empty email (that broke the unique index before)
        doc["email"] = email
    try:
        get_db().users.insert_one(doc)
    except DuplicateKeyError:
        return fail("That username or email is already in use.", 409)
    return ok(message=f"Teacher {name} added.")


@api.delete("/teachers/<username>")
@login_required("admin")
def delete_teacher(username):
    res = get_db().users.delete_one({"username": username, "role": "teacher"})
    return ok(deleted=res.deleted_count)


# =========================================================== Attendance
def _attendance_filters():
    return {k: (request.args.get(k) or "").strip() for k in ("session_id", "date", "student_id")}


@api.get("/attendance")
@login_required("teacher", "admin")
def list_attendance():
    query = {k: v for k, v in _attendance_filters().items() if v}
    rows = list(get_db().attendance.find(query, {"_id": 0}).sort("created_at", -1).limit(1000))
    return jsonify(rows)


@api.get("/attendance/export")
@login_required("teacher", "admin")
def export_attendance():
    buf = ExcelService.build_attendance_workbook(_attendance_filters())
    return send_file(
        buf, as_attachment=True,
        download_name=f"attendance_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@api.get("/student/my_attendance")
@login_required("student")
def my_attendance():
    db = get_db()
    sid = session["user"].get("student_id")
    rows = list(db.attendance.find({"student_id": sid}, {"_id": 0}).sort("created_at", -1))
    attended = len({r["session_id"] for r in rows})
    total = max(db.sessions.count_documents({"status": "ENDED"}), attended)
    percent = round(100.0 * attended / total, 1) if total else 0.0
    return jsonify({"records": rows, "attended": attended, "total_sessions": total, "percent": percent})
