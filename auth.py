from functools import wraps
from flask import session, redirect, url_for, jsonify, request

HOME = {"admin": "pages.admin_page", "teacher": "pages.teacher_page", "student": "pages.student_page"}


def home_for(role):
    return url_for(HOME.get(role, "pages.login"))


def login_required(*roles):
    """Usage: @login_required("teacher", "admin").  No roles = any logged-in user."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = session.get("user")
            is_api = request.path.startswith("/api/")
            if not user:
                if is_api:
                    return jsonify({"success": False, "message": "Please log in."}), 401
                return redirect(url_for("pages.login"))
            if roles and user["role"] not in roles:
                if is_api:
                    return jsonify({"success": False, "message": "You do not have access to this."}), 403
                return redirect(home_for(user["role"]))
            return fn(*args, **kwargs)
        return wrapper
    return decorator
