import os
import logging
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException
from config import Config
from database import Database

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    os.makedirs(Config.UPLOADS_DIR, exist_ok=True)

    db = Database.initialize()
    if db is not None:
        # The server was restarted, so any session still marked ACTIVE is really over.
        db.sessions.update_many({"status": "ACTIVE"}, {"$set": {"status": "ENDED"}})

    from app.routes.pages import pages
    from app.routes.api import api
    from app.routes.settings import settings_bp, load_saved_camera_settings
    app.register_blueprint(pages)
    app.register_blueprint(api)
    app.register_blueprint(settings_bp)

    # Camera details saved from the admin "Camera settings" page override .env.
    load_saved_camera_settings()

    @app.errorhandler(Exception)
    def handle_error(e):
        if isinstance(e, HTTPException):
            code, msg = e.code, e.description
        else:
            logger.exception("Unhandled error")
            code, msg = 500, "Internal server error"
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "message": msg}), code
        return (e if isinstance(e, HTTPException) else ("Internal server error", 500))

    # Start the camera thread at boot so the first page load never waits for it.
    from services.cctv_service import CCTVService
    CCTVService().start()

    logger.info("Flask application initialised.")
    return app