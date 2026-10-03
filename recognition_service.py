"""Real face recognition using OpenCV's YuNet (detector) + SFace (128-d embeddings).

Needs OpenCV >= 4.8 and the two .onnx files in ./models  (run: python download_models.py)
"""
import os
import logging
import threading
import cv2
import numpy as np
from config import Config

logger = logging.getLogger(__name__)

DET_MODEL = "face_detection_yunet_2023mar.onnx"
REC_MODEL = "face_recognition_sface_2021dec.onnx"
MAX_DETECT_WIDTH = 1280  # frames wider than this are downscaled for detection only


class Gallery:
    """All enrolled students' embeddings packed into one matrix for fast matching."""

    def __init__(self, students):
        self.meta = []
        rows, owners = [], []
        for s in students:
            encs = s.get("face_encodings") or []
            if not encs:
                continue
            self.meta.append({"student_id": s["student_id"], "name": s.get("name", "")})
            for e in encs:
                rows.append(np.asarray(e, dtype=np.float32))
                owners.append(len(self.meta) - 1)
        self.matrix = np.vstack(rows) if rows else np.zeros((0, 128), np.float32)
        self.owners = np.asarray(owners, dtype=np.int32)

    def __len__(self):
        return len(self.meta)


class RecognitionService:
    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls):
        """Shared instance (raises FileNotFoundError if the models are missing)."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        det_path = os.path.join(Config.MODELS_DIR, DET_MODEL)
        rec_path = os.path.join(Config.MODELS_DIR, REC_MODEL)
        for p in (det_path, rec_path):
            if not os.path.exists(p) or os.path.getsize(p) < 100_000:
                raise FileNotFoundError(f"Model file missing or invalid: {p}. Run: python download_models.py")
        self._lock = threading.Lock()  # OpenCV detector/recognizer objects are not thread-safe
        self._det = cv2.FaceDetectorYN.create(det_path, "", (320, 320), Config.MIN_FACE_SCORE, 0.3, 5000)
        self._rec = cv2.FaceRecognizerSF.create(rec_path, "")
        logger.info("Face models loaded (YuNet + SFace).")

    # ------------------------------------------------------------------ helpers
    def detect(self, frame):
        """Returns a list of face rows: [x, y, w, h, 5 landmarks (x,y)..., score] in frame coordinates."""
        h, w = frame.shape[:2]
        scale = min(1.0, MAX_DETECT_WIDTH / float(w))
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame
        with self._lock:
            self._det.setInputSize((small.shape[1], small.shape[0]))
            _, faces = self._det.detect(small)
        if faces is None:
            return []
        faces = faces.copy()
        faces[:, :14] /= scale  # back to full-frame coordinates
        return list(faces)

    def _embed(self, frame, face):
        with self._lock:
            aligned = self._rec.alignCrop(frame, face)
            feat = self._rec.feature(aligned)
        vec = np.asarray(feat, dtype=np.float32).flatten()
        n = np.linalg.norm(vec)
        return vec / n if n > 0 else vec  # L2-normalised: dot product == cosine similarity

    # ------------------------------------------------------------------ registration
    def extract_single_encoding(self, frame):
        """(encoding_list, "OK") if the image has exactly one face, else (None, reason)."""
        if frame is None:
            return None, "Unreadable image (is the camera connected?)"
        faces = self.detect(frame)
        if len(faces) == 0:
            return None, "No face detected"
        if len(faces) > 1:
            return None, f"{len(faces)} faces detected (need exactly 1)"
        return self._embed(frame, faces[0]).tolist(), "OK"

    # ------------------------------------------------------------------ live recognition
    def recognize(self, frame, gallery):
        """One result per detected face:
        {"box": (x, y, w, h), "recognized": bool, "student_id", "name", "score"}"""
        results = []
        for face in self.detect(frame):
            x, y, w, h = [int(v) for v in face[:4]]
            item = {"box": (x, y, w, h), "recognized": False, "student_id": None, "name": "Unknown", "score": 0.0}
            if len(gallery):
                emb = self._embed(frame, face)
                sims = gallery.matrix @ emb
                best = np.array([sims[gallery.owners == i].max() for i in range(len(gallery))])
                order = np.argsort(-best)
                top = int(order[0])
                top_s = float(best[top])
                second_s = float(best[order[1]]) if len(order) > 1 else -1.0
                item["score"] = round(top_s, 3)
                # must beat the threshold AND be clearly better than the runner-up student
                if top_s >= Config.RECOGNITION_THRESHOLD and (top_s - second_s) >= Config.RECOGNITION_MARGIN:
                    m = gallery.meta[top]
                    item.update(recognized=True, student_id=m["student_id"], name=m["name"])
            results.append(item)
        return results
