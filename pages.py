from flask import Blueprint, render_template, request, redirect, url_for, session
from werkzeug.security import check_password_hash
from database import Database
from app.auth import login_required, home_for

pages = Blueprint("pages", __name__)


@pages.get("/")
def index():
    user = session.get("user")
    return redirect(home_for(user["role"]) if user else url_for("pages.login"))


@pages.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        db = Database.get_db()
        if db is None:
            error = "The database is not reachable. Check that MongoDB is running."
        else:
            user = db.users.find_one({"username": username})
            if user and check_password_hash(user.get("password_hash", ""), password):
                session.clear()
                session["user"] = {
                    "username": user["username"],
                    "name": user.get("name", user["username"]),
                    "role": user["role"],
                    "student_id": user.get("student_id"),
                }
                return redirect(home_for(user["role"]))
            error = "Wrong username or password."
    return render_template("login.html", error=error)


@pages.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("pages.login"))


@pages.get("/teacher")
@login_required("teacher", "admin")
def teacher_page():
    return render_template("teacher.html")


@pages.get("/admin")
@login_required("admin")
def admin_page():
    return render_template("admin.html")


@pages.get("/student")
@login_required("student")
def student_page():
    return render_template("student.html")
