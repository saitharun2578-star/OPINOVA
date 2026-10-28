"""
Opinova — Database Layer & Schema Management
SQLite-based persistent storage with zero student identifying data,
role-based data integrity, safe schema migrations, and academic structure management.
"""

import os
import sqlite3
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opinova.db")

# Anonymity threshold: Minimum responses required to display individual faculty breakdown
ANONYMITY_THRESHOLD = 5

def get_db_connection():
    """Returns a SQLite connection with foreign keys enabled and Row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    """Initializes tables, indexes, safe migrations, and initial admin configuration."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Subjects table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        code TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Lecturers / Faculty table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lecturers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        department TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. Subject-Lecturer Relationships
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subject_lecturers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        lecturer_id INTEGER NOT NULL REFERENCES lecturers(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(subject_id, lecturer_id)
    );
    """)

    # 4. Feedback table
    # CRITICAL: NO IP address, NO user agent, NO student ID, NO names or emails stored!
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
        lecturer_id INTEGER REFERENCES lecturers(id) ON DELETE SET NULL,
        rating_overall INTEGER NOT NULL CHECK(rating_overall BETWEEN 1 AND 5),
        rating_teaching_clarity INTEGER NOT NULL CHECK(rating_teaching_clarity BETWEEN 1 AND 5),
        rating_course_content INTEGER NOT NULL CHECK(rating_course_content BETWEEN 1 AND 5),
        rating_classroom_env INTEGER NOT NULL CHECK(rating_classroom_env BETWEEN 1 AND 5),
        rating_student_engagement INTEGER NOT NULL CHECK(rating_student_engagement BETWEEN 1 AND 5),
        comment_working_well TEXT,
        comment_improvements TEXT,
        comment_suggestions TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_demo INTEGER DEFAULT 0
    );
    """)

    # 5. Improvement Tracking table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS improvements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
        lecturer_id INTEGER REFERENCES lecturers(id) ON DELETE SET NULL,
        status TEXT NOT NULL CHECK(status IN ('Identified', 'In Progress', 'Resolved')) DEFAULT 'Identified',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 6. Admin Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Non-destructive migrations: Add is_active column if missing from existing database
    cursor.execute("PRAGMA table_info(subjects);")
    sub_cols = [r[1] for r in cursor.fetchall()]
    if "is_active" not in sub_cols:
        cursor.execute("ALTER TABLE subjects ADD COLUMN is_active INTEGER DEFAULT 1;")

    cursor.execute("PRAGMA table_info(lecturers);")
    lect_cols = [r[1] for r in cursor.fetchall()]
    if "is_active" not in lect_cols:
        cursor.execute("ALTER TABLE lecturers ADD COLUMN is_active INTEGER DEFAULT 1;")

    # Indexes for fast querying
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback(created_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_subject ON feedback(subject_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_lecturer ON feedback(lecturer_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_demo ON feedback(is_demo);")

    # Check if admin user exists; if not, create configured administrator
    cursor.execute("SELECT COUNT(*) FROM admin_users;")
    admin_count = cursor.fetchone()[0]
    admin_user = os.getenv("OPINOVA_ADMIN_USER", "opinova")
    admin_pass = os.getenv("OPINOVA_ADMIN_PASSWORD", "123456789")
    if admin_count == 0:
        hashed = generate_password_hash(admin_pass)
        cursor.execute("INSERT INTO admin_users (username, password_hash) VALUES (?, ?);", (admin_user, hashed))

    conn.commit()
    conn.close()

# ----------------- Subject Management Methods ----------------- #

def get_subjects(only_active=False):
    """
    Retrieve subjects dynamically.
    If only_active=True, returns only active subjects (for student feedback form).
    Otherwise returns all subjects with assigned faculty counts and feedback counts.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    if only_active:
        cursor.execute("""
            SELECT id, name, code, is_active, created_at
            FROM subjects
            WHERE is_active = 1
            ORDER BY name ASC;
        """)
    else:
        cursor.execute("""
            SELECT s.id, s.name, s.code, s.is_active, s.created_at,
                   COUNT(DISTINCT sl.lecturer_id) AS faculty_count,
                   COUNT(DISTINCT f.id) AS feedback_count
            FROM subjects s
            LEFT JOIN subject_lecturers sl ON s.id = sl.subject_id
            LEFT JOIN feedback f ON s.id = f.subject_id AND f.is_demo = 0
            GROUP BY s.id
            ORDER BY s.name ASC;
        """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_subject_by_id(subject_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, code, is_active FROM subjects WHERE id = ?;", (subject_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def add_subject(name, code=None, is_active=1):
    name = (name or "").strip()
    code = (code or "").strip() if code else None
    if not name:
        raise ValueError("Subject name cannot be empty.")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO subjects (name, code, is_active) VALUES (?, ?, ?);", (name, code, 1 if is_active else 0))
        conn.commit()
        new_id = cursor.lastrowid
        return new_id
    finally:
        conn.close()

def update_subject(subject_id, name, code=None, is_active=None):
    name = (name or "").strip()
    code = (code or "").strip() if code else None
    if not name:
        raise ValueError("Subject name cannot be empty.")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if is_active is not None:
            cursor.execute(
                "UPDATE subjects SET name = ?, code = ?, is_active = ? WHERE id = ?;",
                (name, code, 1 if is_active else 0, subject_id)
            )
        else:
            cursor.execute(
                "UPDATE subjects SET name = ?, code = ? WHERE id = ?;",
                (name, code, subject_id)
            )
        conn.commit()
    finally:
        conn.close()

def toggle_subject_status(subject_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE subjects SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?;", (subject_id,))
    conn.commit()
    cursor.execute("SELECT is_active FROM subjects WHERE id = ?;", (subject_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row["is_active"]) if row else False

def delete_subject(subject_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM subjects WHERE id = ?;", (subject_id,))
    conn.commit()
    conn.close()

# ----------------- Faculty / Lecturer Management Methods ----------------- #

def get_lecturers(subject_id=None, only_active=False):
    """
    Retrieve faculty members dynamically.
    - If subject_id is provided, returns ONLY faculty mapped to that specific subject.
    - If only_active=True, filters for is_active = 1.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if subject_id:
        active_clause = "AND l.is_active = 1" if only_active else ""
        cursor.execute(f"""
            SELECT l.id, l.name, l.department, l.is_active
            FROM lecturers l
            JOIN subject_lecturers sl ON l.id = sl.lecturer_id
            WHERE sl.subject_id = ? {active_clause}
            ORDER BY l.name ASC;
        """, (subject_id,))
    else:
        active_clause = "WHERE l.is_active = 1" if only_active else ""
        cursor.execute(f"""
            SELECT l.id, l.name, l.department, l.is_active, l.created_at,
                   GROUP_CONCAT(DISTINCT s.name) AS subjects_taught,
                   COUNT(DISTINCT sl.subject_id) AS subject_count,
                   COUNT(DISTINCT f.id) AS feedback_count
            FROM lecturers l
            LEFT JOIN subject_lecturers sl ON l.id = sl.lecturer_id
            LEFT JOIN subjects s ON sl.subject_id = s.id
            LEFT JOIN feedback f ON l.id = f.lecturer_id AND f.is_demo = 0
            {active_clause}
            GROUP BY l.id
            ORDER BY l.name ASC;
        """)

    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_lecturer_by_id(lecturer_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT l.id, l.name, l.department, l.is_active
        FROM lecturers l
        WHERE l.id = ?;
    """, (lecturer_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
    res = dict(row)

    cursor.execute("SELECT subject_id FROM subject_lecturers WHERE lecturer_id = ?;", (lecturer_id,))
    res["subject_ids"] = [r["subject_id"] for r in cursor.fetchall()]
    conn.close()
    return res

def add_lecturer(name, department=None, subject_ids=None, is_active=1):
    name = (name or "").strip()
    department = (department or "").strip() if department else None
    if not name:
        raise ValueError("Faculty name cannot be empty.")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO lecturers (name, department, is_active) VALUES (?, ?, ?);", (name, department, 1 if is_active else 0))
        lecturer_id = cursor.lastrowid
        if subject_ids:
            for s_id in subject_ids:
                if s_id:
                    cursor.execute("""
                        INSERT OR IGNORE INTO subject_lecturers (subject_id, lecturer_id)
                        VALUES (?, ?);
                    """, (s_id, lecturer_id))
        conn.commit()
        return lecturer_id
    finally:
        conn.close()

def update_lecturer(lecturer_id, name, department=None, is_active=None, subject_ids=None):
    name = (name or "").strip()
    department = (department or "").strip() if department else None
    if not name:
        raise ValueError("Faculty name cannot be empty.")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if is_active is not None:
            cursor.execute(
                "UPDATE lecturers SET name = ?, department = ?, is_active = ? WHERE id = ?;",
                (name, department, 1 if is_active else 0, lecturer_id)
            )
        else:
            cursor.execute(
                "UPDATE lecturers SET name = ?, department = ? WHERE id = ?;",
                (name, department, lecturer_id)
            )
        
        if subject_ids is not None:
            # Refresh associations
            cursor.execute("DELETE FROM subject_lecturers WHERE lecturer_id = ?;", (lecturer_id,))
            for s_id in subject_ids:
                if s_id:
                    cursor.execute(
                        "INSERT OR IGNORE INTO subject_lecturers (subject_id, lecturer_id) VALUES (?, ?);",
                        (s_id, lecturer_id)
                    )
        conn.commit()
    finally:
        conn.close()

def toggle_lecturer_status(lecturer_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE lecturers SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?;", (lecturer_id,))
    conn.commit()
    cursor.execute("SELECT is_active FROM lecturers WHERE id = ?;", (lecturer_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row["is_active"]) if row else False

def delete_lecturer(lecturer_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM lecturers WHERE id = ?;", (lecturer_id,))
    conn.commit()
    conn.close()

# ----------------- Subject-Faculty Assignments ----------------- #

def get_assignments():
    """
    Returns full mapping of subjects and their assigned faculty.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sl.id, sl.subject_id, sl.lecturer_id, sl.created_at,
               s.name AS subject_name, s.code AS subject_code, s.is_active AS subject_active,
               l.name AS lecturer_name, l.department AS lecturer_department, l.is_active AS lecturer_active
        FROM subject_lecturers sl
        JOIN subjects s ON sl.subject_id = s.id
        JOIN lecturers l ON sl.lecturer_id = l.id
        ORDER BY s.name ASC, l.name ASC;
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def assign_lecturer_to_subject(subject_id, lecturer_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM subjects WHERE id = ?;", (subject_id,))
        if not cursor.fetchone():
            raise ValueError("Subject does not exist.")
        cursor.execute("SELECT id FROM lecturers WHERE id = ?;", (lecturer_id,))
        if not cursor.fetchone():
            raise ValueError("Faculty member does not exist.")

        cursor.execute("""
            INSERT OR IGNORE INTO subject_lecturers (subject_id, lecturer_id)
            VALUES (?, ?);
        """, (subject_id, lecturer_id))
        conn.commit()
        return True
    finally:
        conn.close()

def unassign_lecturer_from_subject(subject_id, lecturer_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM subject_lecturers WHERE subject_id = ? AND lecturer_id = ?;
    """, (subject_id, lecturer_id))
    conn.commit()
    conn.close()
    return True

# ----------------- Bulk Import Helper ----------------- #

def import_bulk_data(subjects_data, lecturers_data, relationships_data):
    """
    Allows the administrator to import real subjects, faculty, and their relationships
    without changing any application code.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    results = {"subjects_added": 0, "lecturers_added": 0, "links_added": 0}

    try:
        # Import Subjects
        subject_name_to_id = {}
        for s in subjects_data:
            s_name = s.get("name", "").strip()
            s_code = s.get("code", "").strip() if s.get("code") else None
            if s_name:
                cursor.execute("INSERT OR IGNORE INTO subjects (name, code, is_active) VALUES (?, ?, 1);", (s_name, s_code))
                cursor.execute("SELECT id FROM subjects WHERE name = ?;", (s_name,))
                row = cursor.fetchone()
                if row:
                    subject_name_to_id[s_name.lower()] = row["id"]
                    results["subjects_added"] += 1

        # Import Lecturers
        lecturer_name_to_id = {}
        for l in lecturers_data:
            l_name = l.get("name", "").strip()
            l_dept = l.get("department", "").strip() if l.get("department") else None
            if l_name:
                cursor.execute("INSERT OR IGNORE INTO lecturers (name, department, is_active) VALUES (?, ?, 1);", (l_name, l_dept))
                cursor.execute("SELECT id FROM lecturers WHERE name = ?;", (l_name,))
                row = cursor.fetchone()
                if row:
                    lecturer_name_to_id[l_name.lower()] = row["id"]
                    results["lecturers_added"] += 1

        # Link Relationships
        for rel in relationships_data:
            s_id = rel.get("subject_id")
            l_id = rel.get("lecturer_id")
            s_name = (rel.get("subject_name") or "").strip().lower()
            l_name = (rel.get("lecturer_name") or "").strip().lower()

            if not s_id and s_name in subject_name_to_id:
                s_id = subject_name_to_id[s_name]
            if not l_id and l_name in lecturer_name_to_id:
                l_id = lecturer_name_to_id[l_name]

            if s_id and l_id:
                cursor.execute("""
                    INSERT OR IGNORE INTO subject_lecturers (subject_id, lecturer_id)
                    VALUES (?, ?);
                """, (s_id, l_id))
                results["links_added"] += 1

        conn.commit()
    finally:
        conn.close()

    return results

# ----------------- Feedback Methods ----------------- #

def submit_feedback(data, is_demo=0):
    """
    Saves an anonymous feedback record.
    Strictly validates 1-5 ratings.
    Stores ZERO student identity markers.
    """
    subject_id = data.get("subject_id") or None
    lecturer_id = data.get("lecturer_id") or None

    rating_overall = int(data["rating_overall"])
    rating_teaching = int(data["rating_teaching_clarity"])
    rating_content = int(data["rating_course_content"])
    rating_env = int(data["rating_classroom_env"])
    rating_engagement = int(data["rating_student_engagement"])

    # Validate 1-5 bounds
    for r in [rating_overall, rating_teaching, rating_content, rating_env, rating_engagement]:
        if r < 1 or r > 5:
            raise ValueError("All ratings must be integers between 1 and 5.")

    # Validate subject and lecturer existence if provided
    conn = get_db_connection()
    cursor = conn.cursor()
    if subject_id:
        cursor.execute("SELECT id FROM subjects WHERE id = ?;", (subject_id,))
        if not cursor.fetchone():
            conn.close()
            raise ValueError("Selected subject does not exist.")
            
    if lecturer_id:
        cursor.execute("SELECT id FROM lecturers WHERE id = ?;", (lecturer_id,))
        if not cursor.fetchone():
            conn.close()
            raise ValueError("Selected faculty member does not exist.")

    cursor.execute("""
        INSERT INTO feedback (
            subject_id, lecturer_id, rating_overall,
            rating_teaching_clarity, rating_course_content,
            rating_classroom_env, rating_student_engagement,
            comment_working_well, comment_improvements,
            comment_suggestions, is_demo
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        subject_id,
        lecturer_id,
        rating_overall,
        rating_teaching,
        rating_content,
        rating_env,
        rating_engagement,
        (data.get("comment_working_well") or "").strip() or None,
        (data.get("comment_improvements") or "").strip() or None,
        (data.get("comment_suggestions") or "").strip() or None,
        is_demo
    ))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_feedback_records(filters=None):
    """
    Retrieves feedback records matching optional filters:
    - subject_id
    - lecturer_id
    - rating_group ('positive', 'neutral', 'negative')
    - date_from, date_to
    - search_query (searches written comments)
    - is_demo (0 = real feedback, 1 = demo data only)
    """
    filters = filters or {}
    conn = get_db_connection()
    cursor = conn.cursor()

    conditions = ["f.is_demo = ?"]
    params = [filters.get("is_demo", 0)]

    if filters.get("subject_id"):
        conditions.append("f.subject_id = ?")
        params.append(filters["subject_id"])

    if filters.get("lecturer_id"):
        conditions.append("f.lecturer_id = ?")
        params.append(filters["lecturer_id"])

    rating_group = filters.get("rating_group")
    if rating_group == "positive":
        conditions.append("f.rating_overall IN (4, 5)")
    elif rating_group == "neutral":
        conditions.append("f.rating_overall = 3")
    elif rating_group == "negative":
        conditions.append("f.rating_overall IN (1, 2)")

    if filters.get("date_from"):
        conditions.append("f.created_at >= ?")
        params.append(filters["date_from"])

    if filters.get("date_to"):
        conditions.append("f.created_at <= ?")
        params.append(filters["date_to"] + " 23:59:59")

    if filters.get("search"):
        search_term = f"%{filters['search'].strip()}%"
        conditions.append("(f.comment_working_well LIKE ? OR f.comment_improvements LIKE ? OR f.comment_suggestions LIKE ?)")
        params.extend([search_term, search_term, search_term])

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT f.id, f.rating_overall, f.rating_teaching_clarity,
               f.rating_course_content, f.rating_classroom_env,
               f.rating_student_engagement, f.comment_working_well,
               f.comment_improvements, f.comment_suggestions,
               f.created_at, f.is_demo,
               s.name AS subject_name,
               l.name AS lecturer_name
        FROM feedback f
        LEFT JOIN subjects s ON f.subject_id = s.id
        LEFT JOIN lecturers l ON f.lecturer_id = l.id
        WHERE {where_clause}
        ORDER BY f.created_at DESC;
    """

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_overview_stats(is_demo=0):
    """
    Computes high-level summary cards for the Admin Overview tab:
    - total_feedback: count of valid feedback submissions
    - avg_overall: average overall satisfaction score
    - avg_category: average score across all 4 specific categories
    - active_subjects_count: number of active subjects
    - active_lecturers_count: number of active faculty members
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Active academic counts
    cursor.execute("SELECT COUNT(*) FROM subjects WHERE is_active = 1;")
    active_subjects = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM lecturers WHERE is_active = 1;")
    active_lecturers = cursor.fetchone()[0]

    # Feedback calculations
    cursor.execute("""
        SELECT COUNT(*),
               AVG(rating_overall),
               AVG(rating_teaching_clarity),
               AVG(rating_course_content),
               AVG(rating_classroom_env),
               AVG(rating_student_engagement)
        FROM feedback
        WHERE is_demo = ?;
    """, (is_demo,))
    fb_row = cursor.fetchone()
    conn.close()

    total_fb = fb_row[0] or 0
    avg_overall = round(fb_row[1], 2) if fb_row[1] else 0.0

    if fb_row[2] is not None:
        cat_scores = [fb_row[2], fb_row[3], fb_row[4], fb_row[5]]
        avg_category = round(sum(cat_scores) / len(cat_scores), 2)
    else:
        avg_category = 0.0

    return {
        "total_feedback": total_fb,
        "avg_overall": avg_overall,
        "avg_category": avg_category,
        "active_subjects": active_subjects,
        "active_faculty": active_lecturers
    }

def get_analytics_summary(filters=None):
    """
    Computes summary metrics, distributions, trends, category averages,
    and faculty comparisons enforcing ANONYMITY_THRESHOLD.
    """
    records = get_feedback_records(filters)
    total_count = len(records)

    # Also get academic inventory counts
    overview = get_overview_stats(filters.get("is_demo", 0) if filters else 0)

    if total_count == 0:
        return {
            "total_responses": 0,
            "average_overall": 0.0,
            "average_category": 0.0,
            "active_subjects": overview["active_subjects"],
            "active_faculty": overview["active_faculty"],
            "positive_count": 0,
            "positive_pct": 0.0,
            "neutral_count": 0,
            "neutral_pct": 0.0,
            "negative_count": 0,
            "negative_pct": 0.0,
            "emoji_distribution": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
            "category_averages": {
                "teaching_clarity": 0.0,
                "course_content": 0.0,
                "classroom_env": 0.0,
                "student_engagement": 0.0
            },
            "trend_data": [],
            "subject_breakdown": [],
            "lecturer_breakdown": [],
            "anonymity_notes": []
        }

    ratings = [r["rating_overall"] for r in records]
    avg_overall = round(sum(ratings) / total_count, 2)

    pos_count = sum(1 for r in ratings if r in (4, 5))
    neu_count = sum(1 for r in ratings if r == 3)
    neg_count = sum(1 for r in ratings if r in (1, 2))

    pos_pct = round((pos_count / total_count) * 100, 1)
    neu_pct = round((neu_count / total_count) * 100, 1)
    neg_pct = round((neg_count / total_count) * 100, 1)

    emoji_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in ratings:
        emoji_dist[r] += 1

    tc_avg = round(sum(r["rating_teaching_clarity"] for r in records) / total_count, 2)
    cc_avg = round(sum(r["rating_course_content"] for r in records) / total_count, 2)
    ce_avg = round(sum(r["rating_classroom_env"] for r in records) / total_count, 2)
    se_avg = round(sum(r["rating_student_engagement"] for r in records) / total_count, 2)

    cat_overall_avg = round((tc_avg + cc_avg + ce_avg + se_avg) / 4.0, 2)

    # Trends over time
    date_groups = {}
    for r in records:
        d = r["created_at"][:10]
        if d not in date_groups:
            date_groups[d] = []
        date_groups[d].append(r["rating_overall"])

    trend_data = []
    for d in sorted(date_groups.keys()):
        d_ratings = date_groups[d]
        trend_data.append({
            "date": d,
            "average": round(sum(d_ratings) / len(d_ratings), 2),
            "count": len(d_ratings)
        })

    # Subject breakdown
    subj_groups = {}
    for r in records:
        s_name = r["subject_name"] or "General / Unspecified"
        if s_name not in subj_groups:
            subj_groups[s_name] = []
        subj_groups[s_name].append(r["rating_overall"])

    subject_breakdown = []
    for s_name, s_ratings in subj_groups.items():
        subject_breakdown.append({
            "subject": s_name,
            "count": len(s_ratings),
            "average": round(sum(s_ratings) / len(s_ratings), 2)
        })
    subject_breakdown.sort(key=lambda x: x["count"], reverse=True)

    # Faculty breakdown (Enforcing anonymity threshold >= 5)
    lect_groups = {}
    for r in records:
        if r["lecturer_name"]:
            l_name = r["lecturer_name"]
            if l_name not in lect_groups:
                lect_groups[l_name] = []
            lect_groups[l_name].append(r["rating_overall"])

    lecturer_breakdown = []
    anonymity_notes = []
    for l_name, l_ratings in lect_groups.items():
        count = len(l_ratings)
        if count >= ANONYMITY_THRESHOLD:
            lecturer_breakdown.append({
                "lecturer": l_name,
                "count": count,
                "average": round(sum(l_ratings) / count, 2),
                "is_masked": False
            })
        else:
            anonymity_notes.append(
                f"Faculty '{l_name}' has {count} response(s). Minimum threshold is {ANONYMITY_THRESHOLD} to safeguard student confidentiality."
            )

    lecturer_breakdown.sort(key=lambda x: x["average"], reverse=True)

    return {
        "total_responses": total_count,
        "average_overall": avg_overall,
        "average_category": cat_overall_avg,
        "active_subjects": overview["active_subjects"],
        "active_faculty": overview["active_faculty"],
        "positive_count": pos_count,
        "positive_pct": pos_pct,
        "neutral_count": neu_count,
        "neutral_pct": neu_pct,
        "negative_count": neg_count,
        "negative_pct": neg_pct,
        "emoji_distribution": emoji_dist,
        "category_averages": {
            "teaching_clarity": tc_avg,
            "course_content": cc_avg,
            "classroom_env": ce_avg,
            "student_engagement": se_avg
        },
        "trend_data": trend_data,
        "subject_breakdown": subject_breakdown,
        "lecturer_breakdown": lecturer_breakdown,
        "anonymity_notes": anonymity_notes
    }

# ----------------- Improvement Tracking ----------------- #

def get_improvements():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.id, i.title, i.description, i.status, i.created_at, i.updated_at,
               s.name AS subject_name,
               l.name AS lecturer_name
        FROM improvements i
        LEFT JOIN subjects s ON i.subject_id = s.id
        LEFT JOIN lecturers l ON i.lecturer_id = l.id
        ORDER BY i.created_at DESC;
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def create_improvement(title, description, subject_id=None, lecturer_id=None, status="Identified"):
    title = (title or "").strip()
    description = (description or "").strip()
    if not title or not description:
        raise ValueError("Title and description are required for improvement items.")
    if status not in ("Identified", "In Progress", "Resolved"):
        status = "Identified"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO improvements (title, description, subject_id, lecturer_id, status)
        VALUES (?, ?, ?, ?, ?);
    """, (title, description, subject_id or None, lecturer_id or None, status))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def update_improvement(item_id, status=None, title=None, description=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []

    if status and status in ("Identified", "In Progress", "Resolved"):
        updates.append("status = ?")
        params.append(status)

    if title:
        updates.append("title = ?")
        params.append(title.strip())

    if description:
        updates.append("description = ?")
        params.append(description.strip())

    params.append(item_id)
    query = f"UPDATE improvements SET {', '.join(updates)} WHERE id = ?;"
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def delete_improvement(item_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM improvements WHERE id = ?;", (item_id,))
    conn.commit()
    conn.close()

# ----------------- Admin Auth & Settings ----------------- #

def verify_admin_credentials(username, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash FROM admin_users WHERE username = ?;", (username,))
    user = cursor.fetchone()
    conn.close()

    if user and check_password_hash(user["password_hash"], password):
        return {"id": user["id"], "username": user["username"]}
    return None

def verify_and_change_admin_password(username, current_password, new_password):
    """
    Safely verifies the current password and hashes the new password.
    Enforces minimum 8-character length.
    """
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters long.")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash FROM admin_users WHERE username = ?;", (username,))
    user = cursor.fetchone()

    if not user or not check_password_hash(user["password_hash"], current_password):
        conn.close()
        raise ValueError("Current password is incorrect.")

    new_hash = generate_password_hash(new_password)
    cursor.execute("UPDATE admin_users SET password_hash = ? WHERE username = ?;", (new_hash, username))
    conn.commit()
    conn.close()
    return True

# ----------------- Demo Mode (Strictly Isolated) ----------------- #

def load_isolated_demo_data():
    """
    Loads an isolated demonstration dataset strictly labeled as DEMO DATA.
    Uses generic labels 'Sample Course 101' and 'Demo Faculty A' so real names are never invented.
    Flagged with is_demo = 1 so it never taints real feedback and can be removed in 1 click.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    clear_demo_data()

    # Demo subjects
    cursor.execute("INSERT INTO subjects (name, code, is_active) VALUES ('[DEMO] Computer Systems', 'CS-101', 1);")
    s1 = cursor.lastrowid
    cursor.execute("INSERT INTO subjects (name, code, is_active) VALUES ('[DEMO] Data Science Foundations', 'DS-201', 1);")
    s2 = cursor.lastrowid

    # Demo faculty
    cursor.execute("INSERT INTO lecturers (name, department, is_active) VALUES ('[DEMO] Professor Alpha', 'Computer Science', 1);")
    l1 = cursor.lastrowid
    cursor.execute("INSERT INTO lecturers (name, department, is_active) VALUES ('[DEMO] Dr. Beta', 'Mathematics', 1);")
    l2 = cursor.lastrowid

    cursor.execute("INSERT INTO subject_lecturers (subject_id, lecturer_id) VALUES (?, ?);", (s1, l1))
    cursor.execute("INSERT INTO subject_lecturers (subject_id, lecturer_id) VALUES (?, ?);", (s2, l2))

    # Add 8 demo feedback submissions (tagged is_demo = 1)
    demo_feedbacks = [
        (s1, l1, 5, 5, 4, 5, 4, "Clear whiteboard derivations and interactive coding live sessions.", "Provide recorded lectures faster after class.", "Add a forum for peer Q&A."),
        (s1, l1, 4, 4, 5, 4, 4, "Very responsive during lab hours and supportive with troubleshooting.", "Pacing of pointers and memory management was slightly rushed.", "Extra practice sets before midterm."),
        (s1, l1, 5, 5, 5, 5, 5, "Brilliant explanations and real-world system architecture examples.", "None, teaching style is great.", "Keep the open office hours."),
        (s1, l1, 4, 4, 4, 4, 5, "Great student engagement and approachable attitude in class.", "Slide decks could be uploaded 24 hours prior.", "More hands-on quizzes."),
        (s1, l1, 5, 5, 4, 4, 4, "Excellent practical homeworks and clear assignment rubrics.", "The second assignment was quite lengthy for a 1-week deadline.", "Consider team projects."),
        (s1, l1, 4, 5, 4, 5, 4, "Very clear tone, energetic lectures, and positive environment.", "Microphone volume had a little echo in room 302.", "Check acoustic setup."),
        (s2, l2, 3, 3, 4, 3, 2, "Comprehensive course syllabus and good textbook recommendations.", "Lectures tend to be monologue-heavy; would love interactive exercises.", "More group discussions."),
        (s2, l2, 2, 2, 3, 2, 3, "Detailed mathematical proofs provided in lecture notes.", "Too fast on linear algebra review; assumes prior advanced mastery.", "Provide recap primer worksheets.")
    ]

    for fb in demo_feedbacks:
        cursor.execute("""
            INSERT INTO feedback (
                subject_id, lecturer_id, rating_overall,
                rating_teaching_clarity, rating_course_content,
                rating_classroom_env, rating_student_engagement,
                comment_working_well, comment_improvements,
                comment_suggestions, is_demo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
        """, fb)

    cursor.execute("""
        INSERT INTO improvements (title, description, subject_id, lecturer_id, status)
        VALUES ('[DEMO ACTION] Upgrade Room 302 Microphone', 'Address acoustic echo reported during morning lectures.', ?, ?, 'In Progress');
    """, (s1, l1))

    cursor.execute("""
        INSERT INTO improvements (title, description, subject_id, lecturer_id, status)
        VALUES ('[DEMO ACTION] Upload Slide Decks 24h Early', 'Release syllabus slides in advance so students can annotate before class.', ?, ?, 'Resolved');
    """, (s1, l1))

    conn.commit()
    conn.close()
    return True

def clear_demo_data():
    """Removes all demo data without affecting any real user-imported records."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM feedback WHERE is_demo = 1;")
    cursor.execute("DELETE FROM improvements WHERE title LIKE '[DEMO%' OR description LIKE '[DEMO%';")
    cursor.execute("DELETE FROM subjects WHERE name LIKE '[DEMO%';")
    cursor.execute("DELETE FROM lecturers WHERE name LIKE '[DEMO%';")
    conn.commit()
    conn.close()
    return True
