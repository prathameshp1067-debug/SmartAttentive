import time
import logging
import threading
from datetime import datetime
from database import Database
from config import Config
from services.cctv_service import CCTVService
from services.recognition_service import RecognitionService, Gallery
from services.attendance_service import AttendanceService

logger = logging.getLogger(__name__)


class SessionWorker:
    """Runs one classroom session: reads frames, recognises faces, marks attendance."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._initialized = False
                cls._instance = inst
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._state_lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self.active_session_id = None
        self.session_info = None
        self.gallery = Gallery([])
        self._overlay = []
        self._overlay_ts = 0.0
        self.cctv = CCTVService()
        self.attendance = AttendanceService()

    # ------------------------------------------------------------------ gallery
    def reload_gallery(self):
        db = Database.get_db()
        if db is None:
            return
        students = list(db.students.find({}, {"_id": 0, "student_id": 1, "name": 1, "face_encodings": 1}))
        self.gallery = Gallery(students)  # replaced atomically; the loop always sees a complete gallery
        logger.info(f"Face gallery loaded: {len(self.gallery)} students.")

    # ------------------------------------------------------------------ session control
    def start_session(self, subject, teacher_name, teacher_id, replace=True):
        """Always begins a brand-new session. If one is running and replace=True it is ended
        (attendance saved) first, so every Start click gives a clean slate."""
        if replace and self.active_session_id:
            self.end_session()
        with self._state_lock:
            if self.active_session_id:
                return False, "A classroom session is already active."
            db = Database.get_db()
            if db is None:
                return False, "Database not connected."
            try:
                RecognitionService.get()
            except Exception as e:
                return False, f"Face models are not ready: {e}"

            self.reload_gallery()
            if len(self.gallery) == 0:
                return False, "No enrolled students with face data. Use 'Add Student Dataset' first."

            now = datetime.now()
            info = {
                "session_id": f"SES_{int(time.time() * 1000)}",
                "subject": subject,
                "teacher_id": teacher_id,
                "teacher_name": teacher_name,
                "date": now.strftime("%Y-%m-%d"),
                "start_time": now.strftime("%H:%M:%S"),
                "end_time": None,
                "status": "ACTIVE",
                "created_at": now.isoformat(timespec="seconds"),
            }
            db.sessions.insert_one(dict(info))
            self.session_info = info
            self.active_session_id = info["session_id"]
            self.attendance.reset()
            self._overlay = []

            self._stop.clear()
            self.cctv.start()
            self._thread = threading.Thread(
                target=self._loop, args=(info["session_id"], subject), name="session-worker", daemon=True)
            self._thread.start()
            logger.info(f"Session started: {info['session_id']} '{subject}' by {teacher_name}")
            return True, info

    def end_session(self):
        with self._state_lock:
            sid = self.active_session_id
            if not sid:
                return False, "No active session to end."
            self._stop.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)

        self.attendance.finalize_session(sid)
        db = Database.get_db()
        if db is not None:
            db.sessions.update_one(
                {"session_id": sid},
                {"$set": {"status": "ENDED", "end_time": datetime.now().strftime("%H:%M:%S")}})
        with self._state_lock:
            self.active_session_id = None
            self.session_info = None
            self._overlay = []
        self.attendance.reset()
        logger.info(f"Session ended: {sid}")
        return True, f"Session {sid} ended."

    # ------------------------------------------------------------------ worker loop
    def _loop(self, session_id, subject):
        rec = RecognitionService.get()
        interval = Config.RECOGNITION_INTERVAL_SECONDS
        logger.info("SessionWorker loop started.")
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                # Only use live frames: a frozen last frame must never keep people "present".
                if self.cctv.is_connected():
                    frame = self.cctv.read_frame()
                    if frame is not None:
                        results = rec.recognize(frame, self.gallery)
                        for r in results:
                            if r["recognized"]:
                                self.attendance.process_recognition(r["student_id"], r["name"], session_id, subject)
                        self._overlay = results
                        self._overlay_ts = time.monotonic()
                self.attendance.check_presence_timeouts()
            except Exception:
                logger.exception("Error in SessionWorker loop")
            self._stop.wait(max(0.05, interval - (time.monotonic() - t0)))
        logger.info("SessionWorker loop terminated.")

    # ------------------------------------------------------------------ read-only info for the API
    def get_overlay(self):
        """Face boxes for the live video (only if fresh)."""
        if time.monotonic() - self._overlay_ts < 2.0:
            return list(self._overlay)
        return []

    def get_status(self):
        presence = self.attendance.get_presence_list()
        return {
            "active": self.active_session_id is not None,
            "session": dict(self.session_info) if self.session_info else None,
            "presence": presence,
            "present_count": len(presence),
        }
