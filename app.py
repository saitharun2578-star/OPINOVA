"""
Opinova — Anonymous Student Feedback & Analysis Platform
Transforming feedback into progress.
Anonymous voices. Meaningful insights. Better learning.
"""

import os
import io
import csv
import json
import time
from functools import wraps
from datetime import datetime, timedelta
from flask import (
    Flask, render_template, request, jsonify, session,
    redirect, url_for, make_response
)
from dotenv import load_dotenv

# Load local environment if .env exists
load_dotenv()

from db import (
    init_db, get_subjects, get_subject_by_id, add_subject, update_subject,
    toggle_subject_status, delete_subject,
    get_lecturers, get_lecturer_by_id, add_lecturer, update_lecturer,
    toggle_lecturer_status, delete_lecturer,
    get_assignments, assign_lecturer_to_subject, unassign_lecturer_from_subject,
    import_bulk_data, submit_feedback, get_feedback_records,
    get_overview_stats, get_analytics_summary,
    get_improvements, create_improvement, update_improvement, delete_improvement,
    verify_admin_credentials, verify_and_change_admin_password,
    load_isolated_demo_data, clear_demo_data, ANONYMITY_THRESHOLD
)
from sentiment import analyze_sentiment_and_themes

app = Flask(__name__)

# Security & Session Configuration
app.config["SECRET_KEY"] = os.getenv("OPINOVA_SECRET_KEY", "opinova-prod-secret-key-2026-auth-token")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# In-memory rate limiting structures
FAILED_LOGINS = {}  # {client_ip: [timestamps]}
SUBMISSION_COOLDOWN = {}  # {nonce: timestamp}

# Run safe initialization and migrations
init_db()

# ----------------- Security Headers & Middleware ----------------- #

@app.after_request
def apply_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "img-src 'self' data:; font-src 'self' https://fonts.gstatic.com;"
    )
    return response

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "admin_user" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized. Administrator session required."}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function

# ----------------- Public Page Routes ----------------- #

@app.route("/")
def index():
    """Opinova Welcome / Landing experience."""
    return render_template("index.html")

@app.route("/feedback")
def feedback_page():
    """Student Anonymous Feedback page (Guided 4-Step Flow)."""
    return render_template("feedback.html")

@app.route("/login")
def login_page():
    """Administrator Login page."""
    if "admin_user" in session:
        return redirect(url_for("dashboard_page"))
    return render_template("login.html")

@app.route("/admin")
@app.route("/dashboard")
@admin_required
def dashboard_page():
    """Administrator Central Management & Analytics Dashboard."""
    return render_template("dashboard.html", admin_user=session.get("admin_user"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ----------------- Student Public APIs ----------------- #

@app.route("/api/subjects", methods=["GET"])
def api_get_subjects():
    """
    Returns active subjects dynamically loaded from the database.
    Only active subjects are shown to students for feedback submissions.
    """
    subjects = get_subjects(only_active=True)
    return jsonify({"subjects": subjects, "count": len(subjects)})

@app.route("/api/lecturers", methods=["GET"])
def api_get_lecturers():
    """
    Returns active faculty members mapped to a specific subject.
    Only displays faculty with an active subject-faculty relationship.
    """
    subject_id = request.args.get("subject_id", type=int)
    lecturers = get_lecturers(subject_id=subject_id, only_active=True)
    return jsonify({"lecturers": lecturers, "count": len(lecturers)})

@app.route("/api/feedback", methods=["POST"])
def api_submit_feedback():
    """
    Accepts anonymous student feedback.
    Strictly guarantees zero storage of IP addresses, cookies, or student identity.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request body."}), 400

    required_ratings = [
        "rating_overall", "rating_teaching_clarity", "rating_course_content",
        "rating_classroom_env", "rating_student_engagement"
    ]
    for field in required_ratings:
        if field not in data or data[field] is None:
            return jsonify({"error": f"Rating field '{field}' is required."}), 400
        try:
            val = int(data[field])
            if val < 1 or val > 5:
                return jsonify({"error": "All rating values must be between 1 and 5."}), 400
        except (ValueError, TypeError):
            return jsonify({"error": f"Invalid rating value for '{field}'."}), 400

    # Submission debouncing
    sub_token = data.get("submission_nonce")
    now = time.time()
    if sub_token:
        if sub_token in SUBMISSION_COOLDOWN and (now - SUBMISSION_COOLDOWN[sub_token]) < 10:
            return jsonify({"error": "Duplicate submission detected. Please wait."}), 429
        SUBMISSION_COOLDOWN[sub_token] = now
        if len(SUBMISSION_COOLDOWN) > 500:
            expired = [k for k, v in SUBMISSION_COOLDOWN.items() if now - v > 900]
            for k in expired:
                del SUBMISSION_COOLDOWN[k]

    try:
        new_id = submit_feedback(data, is_demo=0)
        return jsonify({
            "success": True,
            "message": "Thank you! Your feedback has been securely and anonymously recorded.",
            "submission_id": new_id
        }), 201
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "An error occurred while saving your feedback. Please try again."}), 500

# ----------------- Admin Auth & Settings APIs ----------------- #

@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    """
    Authenticates administrator with brute-force rate-limiting.
    """
    client_ip = request.remote_addr or "client"
    now = time.time()

    attempts = FAILED_LOGINS.get(client_ip, [])
    attempts = [t for t in attempts if now - t < 600]
    FAILED_LOGINS[client_ip] = attempts

    if len(attempts) >= 5:
        return jsonify({
            "error": "Too many failed login attempts. Please wait 10 minutes before trying again."
        }), 429

    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    admin = verify_admin_credentials(username, password)
    if not admin:
        attempts.append(now)
        FAILED_LOGINS[client_ip] = attempts
        return jsonify({"error": "Invalid administrator username or password."}), 401

    if client_ip in FAILED_LOGINS:
        del FAILED_LOGINS[client_ip]

    session.permanent = True
    session["admin_user"] = admin["username"]
    session["admin_id"] = admin["id"]

    return jsonify({"success": True, "message": "Authentication successful.", "user": admin["username"]})

@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully."})

@app.route("/api/admin/change-password", methods=["POST"])
@admin_required
def api_admin_change_password():
    """Allows authenticated administrator to change their password securely."""
    data = request.get_json() or {}
    current_pass = data.get("current_password") or ""
    new_pass = data.get("new_password") or ""
    confirm_pass = data.get("confirm_password") or ""

    if not current_pass or not new_pass:
        return jsonify({"error": "Current password and new password are required."}), 400

    if new_pass != confirm_pass:
        return jsonify({"error": "New password and confirmation do not match."}), 400

    try:
        verify_and_change_admin_password(session["admin_user"], current_pass, new_pass)
        return jsonify({"success": True, "message": "Password changed successfully."})
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "Failed to update password."}), 500

# ----------------- Admin Analytics & Written Feedback APIs ----------------- #

@app.route("/api/admin/overview-stats", methods=["GET"])
@admin_required
def api_admin_overview_stats():
    """Returns top-level metrics for the Overview section."""
    is_demo = request.args.get("is_demo", default=0, type=int)
    stats = get_overview_stats(is_demo=is_demo)
    return jsonify(stats)

@app.route("/api/admin/analytics", methods=["GET"])
@admin_required
def api_admin_analytics():
    """
    Provides comprehensive KPI metrics, emoji distribution, trend data,
    category averages, and rule-based sentiment themes.
    """
    filters = {
        "subject_id": request.args.get("subject_id", type=int),
        "lecturer_id": request.args.get("lecturer_id", type=int),
        "rating_group": request.args.get("rating_group"),
        "date_from": request.args.get("date_from"),
        "date_to": request.args.get("date_to"),
        "is_demo": request.args.get("is_demo", default=0, type=int)
    }

    analytics = get_analytics_summary(filters)
    feedback_rows = get_feedback_records(filters)
    sentiment_insights = analyze_sentiment_and_themes(feedback_rows)

    return jsonify({
        "summary": analytics,
        "sentiment_insights": sentiment_insights,
        "anonymity_threshold": ANONYMITY_THRESHOLD
    })

@app.route("/api/admin/feedback-list", methods=["GET"])
@admin_required
def api_admin_feedback_list():
    """
    Returns anonymous qualitative student feedback records.
    Contains NO IP addresses or student identifiers.
    """
    filters = {
        "subject_id": request.args.get("subject_id", type=int),
        "lecturer_id": request.args.get("lecturer_id", type=int),
        "rating_group": request.args.get("rating_group"),
        "date_from": request.args.get("date_from"),
        "date_to": request.args.get("date_to"),
        "search": request.args.get("search"),
        "is_demo": request.args.get("is_demo", default=0, type=int)
    }
    records = get_feedback_records(filters)
    return jsonify({"feedback": records, "count": len(records)})

# ----------------- Admin Subject Management APIs ----------------- #

@app.route("/api/admin/subjects", methods=["GET"])
@admin_required
def api_admin_get_all_subjects():
    """Returns all subjects with active status, assigned faculty count, and feedback count."""
    subjects = get_subjects(only_active=False)
    return jsonify({"subjects": subjects, "count": len(subjects)})

@app.route("/api/admin/subjects", methods=["POST"])
@admin_required
def api_admin_add_subject():
    data = request.get_json() or {}
    name = data.get("name")
    code = data.get("code")
    is_active = data.get("is_active", 1)
    try:
        new_id = add_subject(name, code, is_active=is_active)
        return jsonify({"success": True, "subject_id": new_id, "message": "Subject added successfully."}), 201
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "Failed to add subject (name may already exist)."}), 400

@app.route("/api/admin/subjects/<int:subject_id>", methods=["PUT"])
@admin_required
def api_admin_update_subject(subject_id):
    data = request.get_json() or {}
    name = data.get("name")
    code = data.get("code")
    is_active = data.get("is_active")
    try:
        update_subject(subject_id, name, code, is_active)
        return jsonify({"success": True, "message": "Subject updated successfully."})
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "Failed to update subject."}), 400

@app.route("/api/admin/subjects/<int:subject_id>/status", methods=["PATCH"])
@admin_required
def api_admin_toggle_subject_status(subject_id):
    new_status = toggle_subject_status(subject_id)
    return jsonify({"success": True, "is_active": new_status, "message": f"Subject status changed to {'Active' if new_status else 'Inactive'}."})

@app.route("/api/admin/subjects/<int:subject_id>", methods=["DELETE"])
@admin_required
def api_admin_delete_subject(subject_id):
    delete_subject(subject_id)
    return jsonify({"success": True, "message": "Subject removed successfully."})

# ----------------- Admin Faculty Management APIs ----------------- #

@app.route("/api/admin/lecturers", methods=["GET"])
@admin_required
def api_admin_get_all_lecturers():
    """Returns all faculty members with subject associations, active status, and feedback counts."""
    lecturers = get_lecturers(only_active=False)
    return jsonify({"lecturers": lecturers, "count": len(lecturers)})

@app.route("/api/admin/lecturers", methods=["POST"])
@admin_required
def api_admin_add_lecturer():
    data = request.get_json() or {}
    name = data.get("name")
    dept = data.get("department")
    subject_ids = data.get("subject_ids", [])
    is_active = data.get("is_active", 1)
    try:
        new_id = add_lecturer(name, dept, subject_ids, is_active=is_active)
        return jsonify({"success": True, "lecturer_id": new_id, "message": "Faculty member added successfully."}), 201
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "Failed to add faculty member."}), 400

@app.route("/api/admin/lecturers/<int:lecturer_id>", methods=["PUT"])
@admin_required
def api_admin_update_lecturer(lecturer_id):
    data = request.get_json() or {}
    name = data.get("name")
    dept = data.get("department")
    is_active = data.get("is_active")
    subject_ids = data.get("subject_ids")
    try:
        update_lecturer(lecturer_id, name, dept, is_active, subject_ids)
        return jsonify({"success": True, "message": "Faculty member updated successfully."})
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "Failed to update faculty member."}), 400

@app.route("/api/admin/lecturers/<int:lecturer_id>/status", methods=["PATCH"])
@admin_required
def api_admin_toggle_lecturer_status(lecturer_id):
    new_status = toggle_lecturer_status(lecturer_id)
    return jsonify({"success": True, "is_active": new_status, "message": f"Faculty status changed to {'Active' if new_status else 'Inactive'}."})

@app.route("/api/admin/lecturers/<int:lecturer_id>", methods=["DELETE"])
@admin_required
def api_admin_delete_lecturer(lecturer_id):
    delete_lecturer(lecturer_id)
    return jsonify({"success": True, "message": "Faculty member removed successfully."})

# ----------------- Admin Subject-Faculty Assignment APIs ----------------- #

@app.route("/api/admin/assignments", methods=["GET"])
@admin_required
def api_admin_get_assignments():
    assignments = get_assignments()
    return jsonify({"assignments": assignments, "count": len(assignments)})

@app.route("/api/admin/assignments", methods=["POST"])
@admin_required
def api_admin_assign_lecturer():
    data = request.get_json() or {}
    subject_id = data.get("subject_id")
    lecturer_id = data.get("lecturer_id")

    if not subject_id or not lecturer_id:
        return jsonify({"error": "Both subject_id and lecturer_id are required."}), 400

    try:
        assign_lecturer_to_subject(int(subject_id), int(lecturer_id))
        return jsonify({"success": True, "message": "Faculty member assigned to subject."})
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "Failed to assign faculty member."}), 400

@app.route("/api/admin/assignments", methods=["DELETE"])
@admin_required
def api_admin_unassign_lecturer():
    data = request.get_json() or {}
    subject_id = data.get("subject_id")
    lecturer_id = data.get("lecturer_id")

    if not subject_id or not lecturer_id:
        return jsonify({"error": "Both subject_id and lecturer_id are required."}), 400

    unassign_lecturer_from_subject(int(subject_id), int(lecturer_id))
    return jsonify({"success": True, "message": "Faculty assignment removed."})

# ----------------- Admin Bulk Import API ----------------- #

@app.route("/api/admin/import-data", methods=["POST"])
@admin_required
def api_admin_import_data():
    """
    Imports real subjects, faculty, and relationships without editing code.
    Accepts CSV upload or JSON payload.
    """
    if "file" in request.files:
        file = request.files["file"]
        content = file.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        subjects_data = []
        lecturers_data = []
        relationships = []

        for row in reader:
            s_name = row.get("subject_name", "").strip()
            s_code = row.get("subject_code", "").strip()
            l_name = row.get("lecturer_name", "").strip()
            l_dept = row.get("department", "").strip()

            if s_name:
                subjects_data.append({"name": s_name, "code": s_code})
            if l_name:
                lecturers_data.append({"name": l_name, "department": l_dept})
            if s_name and l_name:
                relationships.append({"subject_name": s_name, "lecturer_name": l_name})

        result = import_bulk_data(subjects_data, lecturers_data, relationships)
        return jsonify({"success": True, "results": result, "message": f"CSV import finished: {result['subjects_added']} subjects, {result['lecturers_added']} faculty, {result['links_added']} assignments created."})

    data = request.get_json() or {}
    subjects = data.get("subjects", [])
    lecturers = data.get("lecturers", [])
    relationships = data.get("relationships", [])

    result = import_bulk_data(subjects, lecturers, relationships)
    return jsonify({"success": True, "results": result, "message": f"Bulk import finished: {result['subjects_added']} subjects, {result['lecturers_added']} faculty, {result['links_added']} assignments created."})

# ----------------- Improvement Tracking APIs ----------------- #

@app.route("/api/admin/improvements", methods=["GET"])
@admin_required
def api_admin_get_improvements():
    items = get_improvements()
    return jsonify({"improvements": items, "count": len(items)})

@app.route("/api/admin/improvements", methods=["POST"])
@admin_required
def api_admin_create_improvement():
    data = request.get_json() or {}
    try:
        new_id = create_improvement(
            title=data.get("title"),
            description=data.get("description"),
            subject_id=data.get("subject_id"),
            lecturer_id=data.get("lecturer_id"),
            status=data.get("status", "Identified")
        )
        return jsonify({"success": True, "id": new_id, "message": "Improvement item recorded."}), 201
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400

@app.route("/api/admin/improvements/<int:item_id>", methods=["PUT"])
@admin_required
def api_admin_update_improvement(item_id):
    data = request.get_json() or {}
    update_improvement(
        item_id=item_id,
        status=data.get("status"),
        title=data.get("title"),
        description=data.get("description")
    )
    return jsonify({"success": True, "message": "Improvement item updated."})

@app.route("/api/admin/improvements/<int:item_id>", methods=["DELETE"])
@admin_required
def api_admin_delete_improvement(item_id):
    delete_improvement(item_id)
    return jsonify({"success": True, "message": "Improvement item removed."})

# ----------------- Isolated Demo Mode APIs ----------------- #

@app.route("/api/admin/demo-data", methods=["POST"])
@admin_required
def api_admin_load_demo():
    load_isolated_demo_data()
    return jsonify({"success": True, "message": "Isolated demo dataset loaded. Clearly tagged as DEMO DATA."})

@app.route("/api/admin/demo-data", methods=["DELETE"])
@admin_required
def api_admin_clear_demo():
    clear_demo_data()
    return jsonify({"success": True, "message": "Demo data cleared successfully."})

# ----------------- Main Entrypoint ----------------- #

if __name__ == "__main__":
    import socket
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "0.0.0.0")
    debug = os.getenv("FLASK_ENV") == "development"

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        network_ip = s.getsockname()[0]
        s.close()
    except Exception:
        network_ip = "127.0.0.1"

    print(f"[*] Starting Opinova on 0.0.0.0:{port}")
    print(f"[*] Local link:           http://127.0.0.1:{port}")
    print(f"[*] Public Network link:  http://{network_ip}:{port}")
    print(f"[*] Public Feedback link: http://{network_ip}:{port}/feedback")
    app.run(host=host, port=port, debug=debug)
