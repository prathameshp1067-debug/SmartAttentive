import time
import logging
import threading
from datetime import datetime
from database import Database
from config import Config

logger = logging.getLogger(__name__)


class AttendanceService:
    """Tracks who is currently in the room and writes attendance records (thread-safe)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._presence = {}    # student_id -> live info shown in the dashboard
        self._marked = set()   # students already written to MongoDB in this session

    def reset(self):
        with self._lock:
            self._presence.clear()
            self._marked.clear()

    def process_recognition(self, student_id, name, session_id, subject=""):
        """Called for every recognised face. Returns True only when a NEW attendance record is created."""
        now_ts = time.time()
        now_str = datetime.now().strftime("%H:%M:%S")

        with self._lock:
            p = self._presence.get(student_id)
            if p is None:
                p = {"student_id": student_id, "name": name, "entry_time": now_str,
                     "first_seen_ts": now_ts, "detections": 0}
                self._presence[student_id] = p
            p.update(last_seen=now_str, last_seen_ts=now_ts, status="Present")
            p["detections"] += 1
            already = student_id in self._marked

        if already:
            return False

        db = Database.get_db()
        if db is None:
            logger.error("MongoDB unavailable; attendance not saved.")
            return False
        try:
            res = db.attendance.update_one(
                {"student_id": student_id, "session_id": session_id},
                {"$setOnInsert": {
                    "student_name": name,
                    "subject": subject,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "entry_time": now_str,
                    "exit_time": None,
                    "duration_minutes": 0,
                    "status": "Present",
                    "recognition_method": "IP_CCTV_FACE_RECOGNITION",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                }},
                upsert=True,  # unique (student_id, session_id) index makes this safe against duplicates
            )
            with self._lock:
                self._marked.add(student_id)
            if res.upserted_id is not None:
                logger.info(f"Attendance marked: {name} ({student_id}) in {session_id}")
                return True
        except Exception as e:
            logger.error(f"Error saving attendance: {e}")
        return False

    def check_presence_timeouts(self):
        now = time.time()
        with self._lock:
            for p in self._presence.values():
                if now - p["last_seen_ts"] > Config.PRESENCE_TIMEOUT_SECONDS:
                    p["status"] = "Temporarily absent"

    def get_presence_list(self):
        self.check_presence_timeouts()
        with self._lock:
            rows = [{k: v for k, v in p.items() if not k.endswith("_ts")} for p in self._presence.values()]
        return sorted(rows, key=lambda r: r["entry_time"])

    def finalize_session(self, session_id):
        """Writes exit time and duration for everyone seen during the session."""
        db = Database.get_db()
        if db is None:
            return
        with self._lock:
            people = list(self._presence.values())
        for p in people:
            minutes = round((p["last_seen_ts"] - p["first_seen_ts"]) / 60.0, 1)
            try:
                db.attendance.update_one(
                    {"student_id": p["student_id"], "session_id": session_id},
                    {"$set": {"exit_time": p["last_seen"], "duration_minutes": minutes}},
                )
            except Exception as e:
                logger.error(f"finalize_session error for {p['student_id']}: {e}")
