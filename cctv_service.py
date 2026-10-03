import os

# Force RTSP over TCP and a 5 s socket timeout (default is UDP + 30 s hang).
# Must be set before OpenCV opens its first stream.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")

import cv2
import time
import threading
import logging
from config import Config

logger = logging.getLogger(__name__)

STALE_AFTER = 5.0    # seconds without a good frame => report "disconnected" and reconnect
RETRY_MIN   = 2.0    # first retry delay after a failed connection
RETRY_MAX   = 15.0   # retry delay cap


class CCTVService:
    """
    One background thread owns the camera connection (open, read, reconnect).
    Everything else only reads the latest frame / state under a very short lock.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._initialized = False
                cls._instance = inst
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.lock = threading.Lock()
        self.last_frame    = None
        self.last_frame_ts = 0.0
        self.last_frame_time = None
        self.last_error    = None

        self.camera_ip  = Config.CCTV_IP
        self.stream_url = ""
        self.is_running = False

        self.cap = None
        self._thread     = None
        self._start_lock = threading.Lock()
        self._stop       = threading.Event()
        self._reconnect_requested = threading.Event()

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        with self._start_lock:
            t = self._thread
            if t is not None and t.is_alive():
                if not self._stop.is_set():
                    return
                t.join(timeout=5)
            self._stop.clear()
            self.is_running = True
            self._thread = threading.Thread(target=self._worker, name="cctv-capture", daemon=True)
            self._thread.start()

    def release(self):
        self._stop.set()
        self.is_running = False
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3)
        with self.lock:
            self.last_frame    = None
            self.last_frame_ts = 0.0
        logger.info("CCTV camera stream released.")

    # ------------------------------------------------------------------ worker thread
    def _open(self):
        url = Config.get_cctv_stream_url()
        self.stream_url = url
        if not url:
            raise RuntimeError("CCTV stream URL is not configured. Set CCTV_IP and CCTV_PASSWORD in .env")

        masked = Config.get_masked_cctv_url()
        logger.info(f"Connecting to CCTV stream: {masked}")

        try:
            cap = cv2.VideoCapture(
                url, cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000,
                 cv2.CAP_PROP_READ_TIMEOUT_MSEC,  8000],
            )
        except Exception:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)

        if not cap.isOpened():
            cap.release()
            raise RuntimeError(
                f"Failed to open stream at {masked}. "
                "Check CCTV_PASSWORD and CCTV_STREAM_PATH in .env, "
                "or run: python test_camera.py"
            )

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _read_loop(self, cap):
        last_ok = time.monotonic()
        while not self._stop.is_set():
            if self._reconnect_requested.is_set():
                self._reconnect_requested.clear()
                logger.info("CCTV reconnect requested.")
                return

            ok, frame = cap.read()
            now = time.monotonic()

            if ok and frame is not None:
                with self.lock:
                    self.last_frame      = frame
                    self.last_frame_ts   = now
                    self.last_frame_time = time.strftime("%Y-%m-%d %H:%M:%S")
                    self.last_error      = None
                last_ok = now
            else:
                if now - last_ok > STALE_AFTER:
                    logger.warning(f"CCTV: no valid frame for {STALE_AFTER:.0f}s — reconnecting.")
                    return
                time.sleep(0.05)

    def _worker(self):
        logger.info("CCTV background capture loop started.")
        backoff = RETRY_MIN

        while not self._stop.is_set():
            cap = None
            try:
                cap = self._open()
                self.cap = cap
                backoff = RETRY_MIN
                logger.info("CCTV stream connected successfully.")
                self._read_loop(cap)
            except Exception as e:
                with self.lock:
                    self.last_error = str(e)
                logger.error(f"CCTV error: {e}")
            finally:
                if cap is not None:
                    cap.release()
                self.cap = None

            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, RETRY_MAX)

        logger.info("CCTV background capture loop stopped.")

    # ------------------------------------------------------------------ public API
    def connect(self, wait=0.0):
        self.start()
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and not self.is_connected():
            time.sleep(0.1)
        if self.is_connected():
            return True, "Connected successfully"
        return False, self.last_error or "Connecting..."

    def read_frame(self):
        self.start()
        with self.lock:
            if self.last_frame is None:
                return None
            return self.last_frame.copy()

    def get_latest_frame(self):
        return self.read_frame()

    def is_connected(self):
        with self.lock:
            return (
                self.last_frame is not None
                and (time.monotonic() - self.last_frame_ts) < STALE_AFTER
            )

    def reconnect(self):
        self.start()
        self._reconnect_requested.set()
        return True, "Reconnect requested"

    def get_status(self):
        connected = self.is_connected()
        with self.lock:
            return {
                "connected":        connected,
                "camera_ip":        Config.CCTV_IP,
                "stream_available": connected,
                "last_frame_time":  self.last_frame_time,
                "error":            None if connected else self.last_error,
            }
