from __future__ import annotations

import calendar
import hashlib
import hmac
import html
import io
import json
import mimetypes
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import urllib.parse
import zipfile
from contextlib import contextmanager
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("JY_DATA_DIR", BASE_DIR / "data")).expanduser()
DB_PATH = Path(os.environ.get("JY_DB_PATH", DATA_DIR / "jy.db")).expanduser()
STATIC_DIR = BASE_DIR / "static"
REPORTS_DIR = Path(os.environ.get("JY_REPORTS_DIR", BASE_DIR.parent / "\u4f18\u8d1d\u601d\u62a5\u8868\u5f52\u6863")).expanduser()

ALLOWED_ATTENDANCE = {"present", "leave", "absent", "unmarked"}
ALLOWED_BED = {"used", "not_used"}
SESSION_COOKIE = "jy_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60

DB_LOCK = threading.RLock()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_text() -> str:
    return date.today().isoformat()


def round_money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def pin_hash(pin: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        algo, salt, expected = stored.split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    actual = pin_hash(pin, salt).split("$", 2)[2]
    return hmac.compare_digest(actual, expected)


def public_user(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "active": row["active"],
    }


def require_date(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%d")
    return value


def require_month(value: str) -> str:
    datetime.strptime(value + "-01", "%Y-%m-%d")
    return value


def month_range(month: str) -> tuple[str, str]:
    require_month(month)
    year, mon = [int(x) for x in month.split("-")]
    last_day = calendar.monthrange(year, mon)[1]
    return f"{month}-01", f"{month}-{last_day:02d}"


def dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


@contextmanager
def db_conn():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def execute_script() -> None:
    with DB_LOCK:
        with db_conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS students (
                    permanent_id TEXT PRIMARY KEY,
                    annual_id TEXT NOT NULL UNIQUE,
                    academic_year TEXT NOT NULL,
                    grade TEXT NOT NULL,
                    class_no TEXT NOT NULL,
                    seq_in_class INTEGER,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '\u5728\u8bfb',
                    bed_fee_exempt INTEGER NOT NULL DEFAULT 0,
                    opening_balance REAL NOT NULL DEFAULT 0,
                    import_month TEXT,
                    import_start_date TEXT,
                    source_row INTEGER,
                    source_group_raw TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_students_class
                    ON students (academic_year, grade, class_no, seq_in_class);

                CREATE TABLE IF NOT EXISTS fee_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'teacher')),
                    pin_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_user_sessions_user
                    ON user_sessions (user_id, expires_at);

                CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attendance_date TEXT NOT NULL,
                    permanent_id TEXT NOT NULL,
                    lunch_status TEXT NOT NULL DEFAULT 'unmarked',
                    care_status TEXT NOT NULL DEFAULT 'unmarked',
                    bed_status TEXT NOT NULL DEFAULT 'not_used',
                    event_mark INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT '',
                    submitted_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(attendance_date, permanent_id),
                    FOREIGN KEY(permanent_id) REFERENCES students(permanent_id)
                );

                CREATE INDEX IF NOT EXISTS idx_attendance_date
                    ON attendance (attendance_date, permanent_id);

                CREATE TABLE IF NOT EXISTS student_purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    purchase_date TEXT NOT NULL,
                    permanent_id TEXT NOT NULL,
                    item TEXT NOT NULL,
                    amount REAL NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(purchase_date, permanent_id),
                    FOREIGN KEY(permanent_id) REFERENCES students(permanent_id)
                );

                CREATE INDEX IF NOT EXISTS idx_student_purchases_date
                    ON student_purchases (purchase_date, permanent_id);

                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_date TEXT NOT NULL,
                    permanent_id TEXT NOT NULL,
                    amount REAL NOT NULL,
                    method TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(permanent_id) REFERENCES students(permanent_id)
                );

                CREATE INDEX IF NOT EXISTS idx_payments_date
                    ON payments (payment_date, permanent_id);

                CREATE TABLE IF NOT EXISTS monthly_settlements (
                    month TEXT NOT NULL,
                    permanent_id TEXT NOT NULL,
                    opening_balance REAL NOT NULL,
                    recharge_amount REAL NOT NULL,
                    lunch_days INTEGER NOT NULL,
                    lunch_rate REAL NOT NULL,
                    lunch_fee REAL NOT NULL,
                    care_days INTEGER NOT NULL,
                    care_rate REAL NOT NULL,
                    care_fee REAL NOT NULL,
                    full_day_days INTEGER NOT NULL DEFAULT 0,
                    full_day_rate REAL NOT NULL DEFAULT 50,
                    full_day_fee REAL NOT NULL DEFAULT 0,
                    evening_only_days INTEGER NOT NULL DEFAULT 0,
                    evening_only_rate REAL NOT NULL DEFAULT 25,
                    evening_only_fee REAL NOT NULL DEFAULT 0,
                    bed_monthly_fee REAL NOT NULL DEFAULT 0,
                    bed_fee_exempt INTEGER NOT NULL DEFAULT 0,
                    bed_days INTEGER NOT NULL,
                    bed_rate REAL NOT NULL,
                    bed_fee REAL NOT NULL,
                    shopping_fee REAL NOT NULL DEFAULT 0,
                    total_due REAL NOT NULL,
                    ending_balance REAL NOT NULL,
                    balance_status TEXT NOT NULL,
                    unmarked_count INTEGER NOT NULL DEFAULT 0,
                    generated_at TEXT NOT NULL,
                    PRIMARY KEY(month, permanent_id),
                    FOREIGN KEY(permanent_id) REFERENCES students(permanent_id)
                );

                CREATE INDEX IF NOT EXISTS idx_settlements_month
                    ON monthly_settlements (month, ending_balance);

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_archives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_type TEXT NOT NULL,
                    report_key TEXT NOT NULL,
                    permanent_id TEXT,
                    file_path TEXT NOT NULL,
                    generated_by TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL,
                    UNIQUE(report_type, report_key, permanent_id)
                );

                CREATE TABLE IF NOT EXISTS cost_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cost_date TEXT NOT NULL,
                    cost_type TEXT NOT NULL,
                    item TEXT NOT NULL,
                    amount REAL NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cost_records_date
                    ON cost_records (cost_date, cost_type);
                """
            )
            defaults = {
                "lunch_rate": "30",
                "full_day_rate": "50",
                "evening_only_rate": "25",
                "bed_monthly_fee": "50",
                "care_rate": "25",
                "bed_daily_rate": "50",
            }
            for key, value in defaults.items():
                db.execute(
                    """
                    INSERT OR IGNORE INTO fee_settings(key, value, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (key, value, now_text()),
                )
            ensure_default_admin(db)
            ensure_schema_columns(db)


def audit(
    db: sqlite3.Connection,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: Any = None,
    after: Any = None,
) -> None:
    db.execute(
        """
        INSERT INTO audit_log(actor, action, entity_type, entity_id, before_json, after_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor or "",
            action,
            entity_type,
            entity_id,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            now_text(),
        ),
    )


def ensure_default_admin(db: sqlite3.Connection) -> None:
    existing = db.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
    if existing:
        return
    timestamp = now_text()
    db.execute(
        """
        INSERT INTO users(username, display_name, role, pin_hash, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        ("admin", "\u7ba1\u7406\u5458", "admin", pin_hash("123456"), timestamp, timestamp),
    )


def ensure_schema_columns(db: sqlite3.Connection) -> None:
    def columns(table: str) -> set[str]:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        return {r["name"] for r in rows}

    student_cols = columns("students")
    if "bed_fee_exempt" not in student_cols:
        db.execute("ALTER TABLE students ADD COLUMN bed_fee_exempt INTEGER NOT NULL DEFAULT 0")

    attendance_cols = columns("attendance")
    if "event_mark" not in attendance_cols:
        db.execute("ALTER TABLE attendance ADD COLUMN event_mark INTEGER NOT NULL DEFAULT 0")

    settlement_cols = columns("monthly_settlements")
    additions = {
        "full_day_days": "INTEGER NOT NULL DEFAULT 0",
        "full_day_rate": "REAL NOT NULL DEFAULT 50",
        "full_day_fee": "REAL NOT NULL DEFAULT 0",
        "evening_only_days": "INTEGER NOT NULL DEFAULT 0",
        "evening_only_rate": "REAL NOT NULL DEFAULT 25",
        "evening_only_fee": "REAL NOT NULL DEFAULT 0",
        "bed_monthly_fee": "REAL NOT NULL DEFAULT 0",
        "bed_fee_exempt": "INTEGER NOT NULL DEFAULT 0",
        "shopping_fee": "REAL NOT NULL DEFAULT 0",
    }
    for name, ddl in additions.items():
        if name not in settlement_cols:
            db.execute(f"ALTER TABLE monthly_settlements ADD COLUMN {name} {ddl}")


def user_display(user: dict[str, Any] | None) -> str:
    if not user:
        return "unknown"
    return user.get("display_name") or user.get("username") or "unknown"


def login_user(username: str, pin: str) -> dict[str, Any]:
    username = username.strip()
    if not username or not pin:
        raise HttpError(400, "username and pin are required")
    with DB_LOCK:
        with db_conn() as db:
            user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not user or not user["active"] or not verify_pin(pin, user["pin_hash"]):
                raise HttpError(401, "username or pin is incorrect")
            token = secrets.token_urlsafe(32)
            expires_at = time.time() + SESSION_TTL_SECONDS
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM user_sessions WHERE expires_at < ?", (time.time(),))
            db.execute(
                """
                INSERT INTO user_sessions(token, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (token, user["id"], now_text(), expires_at),
            )
            audit(db, user_display(user), "login", "user", str(user["id"]), None, {"username": username})
            db.execute("COMMIT")
            return {"token": token, "user": public_user(user)}


def logout_token(token: str | None, actor: str = "unknown") -> None:
    if not token:
        return
    with DB_LOCK:
        with db_conn() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
            audit(db, actor, "logout", "session", token[:8], None, None)
            db.execute("COMMIT")


def user_for_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with db_conn() as db:
        row = db.execute(
            """
            SELECT u.*
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at > ? AND u.active = 1
            """,
            (token, time.time()),
        ).fetchone()
    return public_user(row)


def require_admin(user: dict[str, Any] | None) -> None:
    if not user:
        raise HttpError(401, "login required")
    if user["role"] != "admin":
        raise HttpError(403, "admin only")


def require_login_user(user: dict[str, Any] | None) -> None:
    if not user:
        raise HttpError(401, "login required")


def list_users() -> list[dict[str, Any]]:
    with db_conn() as db:
        rows = db.execute(
            """
            SELECT id, username, display_name, role, active, created_at, updated_at
            FROM users
            ORDER BY role, username
            """
        ).fetchall()
    return rows


def upsert_user(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    username = str(payload.get("username") or "").strip()
    display_name = str(payload.get("display_name") or username).strip()
    role = str(payload.get("role") or "teacher").strip()
    pin = str(payload.get("pin") or "")
    active = 1 if payload.get("active", True) else 0
    if not username:
        raise HttpError(400, "username is required")
    if role not in {"admin", "teacher"}:
        raise HttpError(400, "role must be admin or teacher")
    if not display_name:
        display_name = username
    with DB_LOCK:
        with db_conn() as db:
            db.execute("BEGIN IMMEDIATE")
            before = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            timestamp = now_text()
            if before:
                if before["role"] == "admin" and active == 0:
                    active_admins = db.execute(
                        "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND active = 1 AND username != ?",
                        (username,),
                    ).fetchone()["c"]
                    if active_admins == 0:
                        raise HttpError(400, "cannot disable the last active admin")
                if pin:
                    db.execute(
                        """
                        UPDATE users
                        SET display_name = ?, role = ?, pin_hash = ?, active = ?, updated_at = ?
                        WHERE username = ?
                        """,
                        (display_name, role, pin_hash(pin), active, timestamp, username),
                    )
                else:
                    db.execute(
                        """
                        UPDATE users
                        SET display_name = ?, role = ?, active = ?, updated_at = ?
                        WHERE username = ?
                        """,
                        (display_name, role, active, timestamp, username),
                    )
                action = "update user"
            else:
                if not pin:
                    raise HttpError(400, "pin is required for new user")
                db.execute(
                    """
                    INSERT INTO users(username, display_name, role, pin_hash, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (username, display_name, role, pin_hash(pin), active, timestamp, timestamp),
                )
                action = "create user"
            after = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            audit(db, actor, action, "user", username, before, public_user(after))
            db.execute("COMMIT")
            return public_user(after) or {}


def seed_json_path() -> Path | None:
    candidates = [
        BASE_DIR / "data" / "student_import_data.json",
        BASE_DIR.parent / "outputs" / "student_import_20260624" / "student_import_data.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def import_student_records(records: list[dict[str, Any]], actor: str = "\u7cfb\u7edf\u5bfc\u5165", source: str = "upload") -> dict[str, Any]:
    if not isinstance(records, list):
        raise ValueError("records must be an array")
    inserted = 0
    updated = 0
    with DB_LOCK:
        with db_conn() as db:
            db.execute("BEGIN IMMEDIATE")
            for r in records:
                permanent_id = str(r["permanent_id"])
                before = db.execute(
                    "SELECT * FROM students WHERE permanent_id = ?",
                    (permanent_id,),
                ).fetchone()
                status = str(r.get("status") or "\u5728\u8bfb")
                if "\u5728\u8bfb" in status or "\u5f85\u786e\u8ba4" in status:
                    status = "\u5728\u8bfb"
                row = {
                    "permanent_id": permanent_id,
                    "annual_id": str(r["annual_id"]),
                    "academic_year": str(r.get("academic_year") or "25\u5b66\u5e74"),
                    "grade": str(r.get("grade") or ""),
                    "class_no": str(r.get("class_no") or ""),
                    "seq_in_class": int(r.get("seq_in_class") or 0),
                    "name": str(r.get("name_clean") or r.get("name_original") or ""),
                    "status": status,
                    "bed_fee_exempt": 0,
                    "opening_balance": round_money(r.get("recommended_import_balance")),
                    "import_month": str(r.get("settlement_month") or "2026-01"),
                    "import_start_date": str(r.get("import_start_date") or "2026-02-01"),
                    "source_row": int(r.get("source_row") or 0),
                    "source_group_raw": str(r.get("source_group_raw") or ""),
                    "note": "",
                }
                timestamp = now_text()
                if before:
                    db.execute(
                        """
                        UPDATE students SET
                            annual_id = :annual_id,
                            academic_year = :academic_year,
                            grade = :grade,
                            class_no = :class_no,
                            seq_in_class = :seq_in_class,
                            name = :name,
                            status = :status,
                            bed_fee_exempt = COALESCE(bed_fee_exempt, :bed_fee_exempt),
                            opening_balance = :opening_balance,
                            import_month = :import_month,
                            import_start_date = :import_start_date,
                            source_row = :source_row,
                            source_group_raw = :source_group_raw,
                            updated_at = :updated_at
                        WHERE permanent_id = :permanent_id
                        """,
                        {**row, "updated_at": timestamp},
                    )
                    updated += 1
                    audit(db, actor, "update imported student", "student", permanent_id, before, row)
                else:
                    db.execute(
                        """
                        INSERT INTO students(
                            permanent_id, annual_id, academic_year, grade, class_no, seq_in_class,
                            name, status, bed_fee_exempt, opening_balance, import_month, import_start_date,
                            source_row, source_group_raw, note, created_at, updated_at
                        )
                        VALUES (
                            :permanent_id, :annual_id, :academic_year, :grade, :class_no, :seq_in_class,
                            :name, :status, :bed_fee_exempt, :opening_balance, :import_month, :import_start_date,
                            :source_row, :source_group_raw, :note, :created_at, :updated_at
                        )
                        """,
                        {**row, "created_at": timestamp, "updated_at": timestamp},
                    )
                    inserted += 1
                    audit(db, actor, "create imported student", "student", permanent_id, None, row)
            db.execute("COMMIT")
    return {"inserted": inserted, "updated": updated, "total": len(records), "source": source}


def seed_students(actor: str = "\u7cfb\u7edf\u5bfc\u5165") -> dict[str, Any]:
    path = seed_json_path()
    if path is None:
        raise FileNotFoundError("student_import_data.json not found")

    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records", [])
    return import_student_records(records, actor, str(path))


def list_students(params: dict[str, list[str]]) -> list[dict[str, Any]]:
    q = (params.get("q") or [""])[0].strip()
    grade = (params.get("grade") or [""])[0].strip()
    class_no = (params.get("class_no") or [""])[0].strip()
    month = (params.get("month") or [today_text()[:7]])[0].strip()

    where = []
    args: list[Any] = []
    if q:
        where.append("(name LIKE ? OR permanent_id LIKE ? OR annual_id LIKE ?)")
        args.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if grade:
        where.append("grade = ?")
        args.append(grade)
    if class_no:
        where.append("class_no = ?")
        args.append(class_no)
    sql = "SELECT * FROM students"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CAST(grade AS INTEGER), CAST(class_no AS INTEGER), seq_in_class, name"
    with db_conn() as db:
        rows = db.execute(sql, args).fetchall()
        if month:
            start, end = month_range(month)
            stats_rows = db.execute(
                """
                SELECT
                    permanent_id,
                    SUM(CASE WHEN lunch_status = 'present' THEN 1 ELSE 0 END) AS lunch_present_days,
                    SUM(CASE WHEN lunch_status != 'unmarked' THEN 1 ELSE 0 END) AS lunch_recorded_days,
                    SUM(CASE WHEN care_status = 'present' THEN 1 ELSE 0 END) AS care_present_days,
                    SUM(CASE WHEN care_status != 'unmarked' THEN 1 ELSE 0 END) AS care_recorded_days
                FROM attendance
                WHERE attendance_date BETWEEN ? AND ?
                GROUP BY permanent_id
                """,
                (start, end),
            ).fetchall()
            stats = {r["permanent_id"]: r for r in stats_rows}
        else:
            stats = {}
        for row in rows:
            s = stats.get(row["permanent_id"], {})
            row["lunch_present_days"] = int(s.get("lunch_present_days") or 0)
            row["lunch_recorded_days"] = int(s.get("lunch_recorded_days") or 0)
            row["care_present_days"] = int(s.get("care_present_days") or 0)
            row["care_recorded_days"] = int(s.get("care_recorded_days") or 0)
        return rows


def list_classes() -> list[dict[str, Any]]:
    with db_conn() as db:
        return db.execute(
            """
            SELECT academic_year, grade, class_no, COUNT(*) AS student_count
            FROM students
            GROUP BY academic_year, grade, class_no
            ORDER BY CAST(grade AS INTEGER), CAST(class_no AS INTEGER)
            """
        ).fetchall()


def settings_map() -> dict[str, str]:
    with db_conn() as db:
        rows = db.execute("SELECT key, value FROM fee_settings ORDER BY key").fetchall()
    return {r["key"]: r["value"] for r in rows}


def settings_map_from_db(db: sqlite3.Connection) -> dict[str, str]:
    rows = db.execute("SELECT key, value FROM fee_settings ORDER BY key").fetchall()
    return {r["key"]: r["value"] for r in rows}


def update_settings(payload: dict[str, Any], actor: str) -> dict[str, str]:
    allowed = {"lunch_rate", "full_day_rate", "evening_only_rate", "bed_monthly_fee", "care_rate", "bed_daily_rate"}
    with DB_LOCK:
        with db_conn() as db:
            db.execute("BEGIN IMMEDIATE")
            before = settings_map_from_db(db)
            for key in allowed:
                if key in payload:
                    value = str(round_money(payload[key]))
                    db.execute(
                        """
                        INSERT INTO fee_settings(key, value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at
                        """,
                        (key, value, now_text()),
                    )
            after = settings_map_from_db(db)
            audit(db, actor, "鏇存柊鏀惰垂瑙勫垯", "settings", "fee_settings", before, after)
            db.execute("COMMIT")
    return after


def attendance_for_class(attendance_date: str, grade: str, class_no: str) -> list[dict[str, Any]]:
    require_date(attendance_date)
    where = ["s.status != '\u505c\u7528'"]
    args: list[Any] = [attendance_date]
    if grade and grade != "__all__":
        where.append("s.grade = ?")
        args.append(grade)
    if class_no and class_no != "__all__":
        where.append("s.class_no = ?")
        args.append(class_no)
    with db_conn() as db:
        return db.execute(
            f"""
            SELECT
                s.permanent_id,
                s.annual_id,
                s.name,
                s.grade,
                s.class_no,
                s.seq_in_class,
                s.bed_fee_exempt,
                COALESCE(a.lunch_status, 'unmarked') AS lunch_status,
                COALESCE(a.care_status, 'unmarked') AS care_status,
                COALESCE(a.bed_status, 'not_used') AS bed_status,
                COALESCE(a.event_mark, 0) AS event_mark,
                COALESCE(a.note, '') AS note,
                COALESCE(a.operator, '') AS operator,
                a.updated_at,
                COALESCE(p.item, '') AS shopping_item,
                COALESCE(p.amount, 0) AS shopping_amount
            FROM students s
            LEFT JOIN attendance a
                ON a.permanent_id = s.permanent_id
                AND a.attendance_date = ?
            LEFT JOIN student_purchases p
                ON p.permanent_id = s.permanent_id
                AND p.purchase_date = ?
            WHERE {" AND ".join(where)}
            ORDER BY CAST(s.grade AS INTEGER), CAST(s.class_no AS INTEGER), s.seq_in_class, s.name
            """,
            [attendance_date, *args],
        ).fetchall()


def save_attendance_bulk(payload: dict[str, Any]) -> dict[str, Any]:
    attendance_date = require_date(str(payload.get("date") or ""))
    actor = str(payload.get("operator") or "").strip() or "unknown"
    records = payload.get("records") or []
    generate_report = bool(payload.get("generate_report", True))
    if not isinstance(records, list):
        raise ValueError("records must be an array")
    saved = 0
    report_result = None
    report_warning = None
    with DB_LOCK:
        with db_conn() as db:
            db.execute("BEGIN IMMEDIATE")
            for rec in records:
                pid = str(rec.get("permanent_id") or "")
                lunch_status = str(rec.get("lunch_status") or "unmarked")
                care_status = str(rec.get("care_status") or "unmarked")
                bed_status = str(rec.get("bed_status") or "not_used")
                event_mark = 1 if rec.get("event_mark") else 0
                note = str(rec.get("note") or "")
                shopping_item = str(rec.get("shopping_item") or "").strip()
                shopping_amount = round_money(rec.get("shopping_amount"))
                if lunch_status not in ALLOWED_ATTENDANCE:
                    raise ValueError(f"invalid lunch_status: {lunch_status}")
                if care_status not in ALLOWED_ATTENDANCE:
                    raise ValueError(f"invalid care_status: {care_status}")
                if bed_status not in ALLOWED_BED:
                    raise ValueError(f"invalid bed_status: {bed_status}")
                if shopping_item and shopping_amount <= 0:
                    raise ValueError(f"shopping amount must be greater than zero: {pid}")
                if shopping_amount > 0 and not shopping_item:
                    raise ValueError(f"shopping item is required: {pid}")
                student = db.execute(
                    "SELECT permanent_id FROM students WHERE permanent_id = ?",
                    (pid,),
                ).fetchone()
                if not student:
                    raise ValueError(f"student not found: {pid}")
                before = db.execute(
                    """
                    SELECT * FROM attendance
                    WHERE attendance_date = ? AND permanent_id = ?
                    """,
                    (attendance_date, pid),
                ).fetchone()
                after = {
                    "attendance_date": attendance_date,
                    "permanent_id": pid,
                    "lunch_status": lunch_status,
                    "care_status": care_status,
                    "bed_status": bed_status,
                    "event_mark": event_mark,
                    "note": note,
                    "shopping_item": shopping_item,
                    "shopping_amount": shopping_amount,
                    "operator": actor,
                }
                timestamp = now_text()
                db.execute(
                    """
                    INSERT INTO attendance(
                        attendance_date, permanent_id, lunch_status, care_status, bed_status,
                        event_mark, note, operator, submitted_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(attendance_date, permanent_id) DO UPDATE SET
                        lunch_status = excluded.lunch_status,
                        care_status = excluded.care_status,
                        bed_status = excluded.bed_status,
                        event_mark = excluded.event_mark,
                        note = excluded.note,
                        operator = excluded.operator,
                        updated_at = excluded.updated_at
                    """,
                    (
                        attendance_date,
                        pid,
                        lunch_status,
                        care_status,
                        bed_status,
                        event_mark,
                        note,
                        actor,
                        timestamp,
                        timestamp,
                    ),
                )
                purchase_before = db.execute(
                    """
                    SELECT * FROM student_purchases
                    WHERE purchase_date = ? AND permanent_id = ?
                    """,
                    (attendance_date, pid),
                ).fetchone()
                if shopping_item and shopping_amount > 0:
                    db.execute(
                        """
                        INSERT INTO student_purchases(
                            purchase_date, permanent_id, item, amount, note, operator, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, '', ?, ?, ?)
                        ON CONFLICT(purchase_date, permanent_id) DO UPDATE SET
                            item = excluded.item,
                            amount = excluded.amount,
                            operator = excluded.operator,
                            updated_at = excluded.updated_at
                        """,
                        (
                            attendance_date,
                            pid,
                            shopping_item,
                            shopping_amount,
                            actor,
                            timestamp,
                            timestamp,
                        ),
                    )
                    purchase_after = db.execute(
                        """
                        SELECT * FROM student_purchases
                        WHERE purchase_date = ? AND permanent_id = ?
                        """,
                        (attendance_date, pid),
                    ).fetchone()
                    audit(db, actor, "save shopping", "student_purchase", f"{attendance_date}:{pid}", purchase_before, purchase_after)
                elif purchase_before:
                    db.execute(
                        """
                        DELETE FROM student_purchases
                        WHERE purchase_date = ? AND permanent_id = ?
                        """,
                        (attendance_date, pid),
                    )
                    audit(db, actor, "delete shopping", "student_purchase", f"{attendance_date}:{pid}", purchase_before, None)
                audit(db, actor, "save attendance", "attendance", f"{attendance_date}:{pid}", before, after)
                saved += 1
            db.execute("COMMIT")
    if generate_report:
        try:
            report_result = daily_attendance_report(attendance_date, actor)
        except Exception as exc:
            report_warning = str(exc)
    return {"saved": saved, "date": attendance_date, "daily_report": report_result, "report_warning": report_warning}


def create_payment(payload: dict[str, Any]) -> dict[str, Any]:
    payment_date = require_date(str(payload.get("payment_date") or today_text()))
    pid = str(payload.get("permanent_id") or "")
    amount = round_money(payload.get("amount"))
    method = str(payload.get("method") or "")
    note = str(payload.get("note") or "")
    actor = str(payload.get("operator") or "").strip() or "unknown"
    if not pid:
        raise ValueError("please choose a student")
    if amount == 0:
        raise ValueError("payment amount cannot be zero")
    with DB_LOCK:
        with db_conn() as db:
            student = db.execute("SELECT * FROM students WHERE permanent_id = ?", (pid,)).fetchone()
            if not student:
                raise ValueError("student not found")
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                """
                INSERT INTO payments(payment_date, permanent_id, amount, method, note, operator, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (payment_date, pid, amount, method, note, actor, now_text()),
            )
            payment_id = cur.lastrowid
            row = db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
            audit(db, actor, "create payment", "payment", str(payment_id), None, row)
            db.execute("COMMIT")
            return row


def list_payments(params: dict[str, list[str]]) -> list[dict[str, Any]]:
    month = (params.get("month") or [""])[0].strip()
    pid = (params.get("permanent_id") or [""])[0].strip()
    where = []
    args: list[Any] = []
    if month:
        start, end = month_range(month)
        where.append("p.payment_date BETWEEN ? AND ?")
        args.extend([start, end])
    if pid:
        where.append("p.permanent_id = ?")
        args.append(pid)
    sql = """
        SELECT p.*, s.name, s.annual_id
        FROM payments p
        JOIN students s ON s.permanent_id = p.permanent_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY p.payment_date DESC, p.id DESC LIMIT 500"
    with db_conn() as db:
        return db.execute(sql, args).fetchall()


def create_cost_record(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    cost_date = require_date(str(payload.get("cost_date") or today_text()))
    cost_type = str(payload.get("cost_type") or "").strip()
    item = str(payload.get("item") or "").strip()
    amount = round_money(payload.get("amount"))
    note = str(payload.get("note") or "").strip()
    allowed = {"fixed", "variable", "labor", "other"}
    if cost_type not in allowed:
        raise ValueError("invalid cost_type")
    if not item:
        raise ValueError("cost item is required")
    if amount <= 0:
        raise ValueError("cost amount must be greater than zero")
    with DB_LOCK:
        with db_conn() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                """
                INSERT INTO cost_records(cost_date, cost_type, item, amount, note, operator, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (cost_date, cost_type, item, amount, note, actor, now_text()),
            )
            row = db.execute("SELECT * FROM cost_records WHERE id = ?", (cur.lastrowid,)).fetchone()
            audit(db, actor, "create cost", "cost", str(cur.lastrowid), None, row)
            db.execute("COMMIT")
            return row


def cost_type_label(value: str) -> str:
    return {
        "fixed": "\u56fa\u5b9a\u6210\u672c",
        "variable": "\u53d8\u52a8\u6210\u672c",
        "labor": "\u4eba\u5de5\u6210\u672c",
        "other": "\u5176\u4ed6\u6210\u672c",
    }.get(value or "", value or "")


def list_costs(params: dict[str, list[str]]) -> dict[str, Any]:
    month = (params.get("month") or [today_text()[:7]])[0].strip()
    start, end = month_range(month)
    with db_conn() as db:
        rows = db.execute(
            """
            SELECT *
            FROM cost_records
            WHERE cost_date BETWEEN ? AND ?
            ORDER BY cost_date DESC, id DESC
            """,
            (start, end),
        ).fetchall()
        summary_rows = db.execute(
            """
            SELECT cost_type, SUM(amount) AS total, COUNT(*) AS count
            FROM cost_records
            WHERE cost_date BETWEEN ? AND ?
            GROUP BY cost_type
            """,
            (start, end),
        ).fetchall()
    summary = {
        "month": month,
        "fixed_total": 0.0,
        "variable_total": 0.0,
        "labor_total": 0.0,
        "other_total": 0.0,
        "grand_total": 0.0,
        "record_count": len(rows),
    }
    for r in summary_rows:
        key = f"{r['cost_type']}_total"
        if key in summary:
            summary[key] = round_money(r["total"])
        summary["grand_total"] = round_money(summary["grand_total"] + round_money(r["total"]))
    for row in rows:
        row["cost_type_label"] = cost_type_label(row["cost_type"])
    return {"summary": summary, "rows": rows}


def previous_balance_map(db: sqlite3.Connection, month: str) -> dict[str, float]:
    rows = db.execute(
        """
        SELECT ms.permanent_id, ms.ending_balance
        FROM monthly_settlements ms
        JOIN (
            SELECT permanent_id, MAX(month) AS prev_month
            FROM monthly_settlements
            WHERE month < ?
            GROUP BY permanent_id
        ) latest
            ON latest.permanent_id = ms.permanent_id
            AND latest.prev_month = ms.month
        """,
        (month,),
    ).fetchall()
    return {r["permanent_id"]: round_money(r["ending_balance"]) for r in rows}


def generate_settlement(month: str, actor: str = "\u7cfb\u7edf\u6708\u7ed3") -> dict[str, Any]:
    month = require_month(month)
    start, end = month_range(month)
    settings = settings_map()
    lunch_rate = round_money(settings.get("lunch_rate", 30))
    full_day_rate = round_money(settings.get("full_day_rate", 50))
    evening_only_rate = round_money(settings.get("evening_only_rate", settings.get("care_rate", 25)))
    bed_monthly_fee = round_money(settings.get("bed_monthly_fee", settings.get("bed_daily_rate", 50)))

    generated = 0
    with DB_LOCK:
        with db_conn() as db:
            db.execute("BEGIN IMMEDIATE")
            students = db.execute(
                """
                SELECT * FROM students
                WHERE status != '\u505c\u7528'
                ORDER BY CAST(grade AS INTEGER), CAST(class_no AS INTEGER), seq_in_class, name
                """
            ).fetchall()
            prev_map = previous_balance_map(db, month)
            attendance_rows = db.execute(
                """
                SELECT
                    permanent_id,
                    SUM(CASE WHEN lunch_status = 'present' AND care_status != 'present' THEN 1 ELSE 0 END) AS lunch_only_days,
                    SUM(CASE WHEN lunch_status = 'present' AND care_status = 'present' THEN 1 ELSE 0 END) AS full_day_days,
                    SUM(CASE WHEN lunch_status != 'present' AND care_status = 'present' THEN 1 ELSE 0 END) AS evening_only_days,
                    SUM(CASE WHEN bed_status = 'used' THEN 1 ELSE 0 END) AS bed_days,
                    SUM(CASE WHEN lunch_status = 'unmarked' OR care_status = 'unmarked' THEN 1 ELSE 0 END) AS unmarked_count
                FROM attendance
                WHERE attendance_date BETWEEN ? AND ?
                GROUP BY permanent_id
                """,
                (start, end),
            ).fetchall()
            attendance_map = {r["permanent_id"]: r for r in attendance_rows}
            payment_rows = db.execute(
                """
                SELECT permanent_id, SUM(amount) AS recharge_amount
                FROM payments
                WHERE payment_date BETWEEN ? AND ?
                GROUP BY permanent_id
                """,
                (start, end),
            ).fetchall()
            payment_map = {r["permanent_id"]: round_money(r["recharge_amount"]) for r in payment_rows}
            shopping_rows = db.execute(
                """
                SELECT permanent_id, SUM(amount) AS shopping_fee
                FROM student_purchases
                WHERE purchase_date BETWEEN ? AND ?
                GROUP BY permanent_id
                """,
                (start, end),
            ).fetchall()
            shopping_map = {r["permanent_id"]: round_money(r["shopping_fee"]) for r in shopping_rows}

            for student in students:
                pid = student["permanent_id"]
                att = attendance_map.get(pid, {})
                opening = prev_map.get(pid, round_money(student["opening_balance"]))
                recharge = payment_map.get(pid, 0.0)
                shopping_fee = shopping_map.get(pid, 0.0)
                lunch_days = int(att.get("lunch_only_days") or 0)
                full_day_days = int(att.get("full_day_days") or 0)
                evening_only_days = int(att.get("evening_only_days") or 0)
                care_days = evening_only_days
                bed_days = int(att.get("bed_days") or 0)
                unmarked_count = int(att.get("unmarked_count") or 0)
                lunch_fee = round_money(lunch_days * lunch_rate)
                full_day_fee = round_money(full_day_days * full_day_rate)
                evening_only_fee = round_money(evening_only_days * evening_only_rate)
                care_rate = evening_only_rate
                care_fee = evening_only_fee
                bed_fee_exempt = int(student.get("bed_fee_exempt") or 0)
                bed_rate = bed_monthly_fee
                bed_fee = 0.0 if bed_fee_exempt or bed_days <= 0 else bed_monthly_fee
                total_due = round_money(lunch_fee + full_day_fee + evening_only_fee + bed_fee + shopping_fee)
                ending = round_money(opening + recharge - total_due)
                if ending < 0:
                    balance_status = "\u6b20\u8d39"
                elif ending > 0:
                    balance_status = "\u6709\u4f59\u989d"
                else:
                    balance_status = "\u521a\u597d\u6e05\u96f6"
                before = db.execute(
                    """
                    SELECT * FROM monthly_settlements
                    WHERE month = ? AND permanent_id = ?
                    """,
                    (month, pid),
                ).fetchone()
                row = {
                    "month": month,
                    "permanent_id": pid,
                    "opening_balance": opening,
                    "recharge_amount": recharge,
                    "lunch_days": lunch_days,
                    "lunch_rate": lunch_rate,
                    "lunch_fee": lunch_fee,
                    "care_days": care_days,
                    "care_rate": care_rate,
                    "care_fee": care_fee,
                    "full_day_days": full_day_days,
                    "full_day_rate": full_day_rate,
                    "full_day_fee": full_day_fee,
                    "evening_only_days": evening_only_days,
                    "evening_only_rate": evening_only_rate,
                    "evening_only_fee": evening_only_fee,
                    "bed_monthly_fee": bed_monthly_fee,
                    "bed_fee_exempt": bed_fee_exempt,
                    "bed_days": bed_days,
                    "bed_rate": bed_rate,
                    "bed_fee": bed_fee,
                    "shopping_fee": shopping_fee,
                    "total_due": total_due,
                    "ending_balance": ending,
                    "balance_status": balance_status,
                    "unmarked_count": unmarked_count,
                    "generated_at": now_text(),
                }
                db.execute(
                    """
                    INSERT INTO monthly_settlements(
                        month, permanent_id, opening_balance, recharge_amount,
                        lunch_days, lunch_rate, lunch_fee,
                        care_days, care_rate, care_fee,
                        full_day_days, full_day_rate, full_day_fee,
                        evening_only_days, evening_only_rate, evening_only_fee,
                        bed_monthly_fee, bed_fee_exempt,
                        bed_days, bed_rate, bed_fee,
                        shopping_fee, total_due, ending_balance, balance_status, unmarked_count, generated_at
                    )
                    VALUES (
                        :month, :permanent_id, :opening_balance, :recharge_amount,
                        :lunch_days, :lunch_rate, :lunch_fee,
                        :care_days, :care_rate, :care_fee,
                        :full_day_days, :full_day_rate, :full_day_fee,
                        :evening_only_days, :evening_only_rate, :evening_only_fee,
                        :bed_monthly_fee, :bed_fee_exempt,
                        :bed_days, :bed_rate, :bed_fee,
                        :shopping_fee, :total_due, :ending_balance, :balance_status, :unmarked_count, :generated_at
                    )
                    ON CONFLICT(month, permanent_id) DO UPDATE SET
                        opening_balance = excluded.opening_balance,
                        recharge_amount = excluded.recharge_amount,
                        lunch_days = excluded.lunch_days,
                        lunch_rate = excluded.lunch_rate,
                        lunch_fee = excluded.lunch_fee,
                        care_days = excluded.care_days,
                        care_rate = excluded.care_rate,
                        care_fee = excluded.care_fee,
                        full_day_days = excluded.full_day_days,
                        full_day_rate = excluded.full_day_rate,
                        full_day_fee = excluded.full_day_fee,
                        evening_only_days = excluded.evening_only_days,
                        evening_only_rate = excluded.evening_only_rate,
                        evening_only_fee = excluded.evening_only_fee,
                        bed_monthly_fee = excluded.bed_monthly_fee,
                        bed_fee_exempt = excluded.bed_fee_exempt,
                        bed_days = excluded.bed_days,
                        bed_rate = excluded.bed_rate,
                        bed_fee = excluded.bed_fee,
                        shopping_fee = excluded.shopping_fee,
                        total_due = excluded.total_due,
                        ending_balance = excluded.ending_balance,
                        balance_status = excluded.balance_status,
                        unmarked_count = excluded.unmarked_count,
                        generated_at = excluded.generated_at
                    """,
                    row,
                )
                audit(db, actor, "generate settlement", "settlement", f"{month}:{pid}", before, row)
                generated += 1
            db.execute("COMMIT")
    return settlement_summary(month) | {"generated": generated, "month": month}


def settlement_rows(month: str) -> list[dict[str, Any]]:
    month = require_month(month)
    with db_conn() as db:
        return db.execute(
            """
            SELECT
                ms.*,
                s.annual_id,
                s.academic_year,
                s.grade,
                s.class_no,
                s.seq_in_class,
                s.name
            FROM monthly_settlements ms
            JOIN students s ON s.permanent_id = ms.permanent_id
            WHERE ms.month = ?
            ORDER BY CAST(s.grade AS INTEGER), CAST(s.class_no AS INTEGER), s.seq_in_class, s.name
            """,
            (month,),
        ).fetchall()


def settlement_summary(month: str) -> dict[str, Any]:
    month = require_month(month)
    start, end = month_range(month)
    with db_conn() as db:
        students_count = db.execute("SELECT COUNT(*) AS c FROM students WHERE status != '\u505c\u7528'").fetchone()["c"]
        attendance_count = db.execute(
            "SELECT COUNT(*) AS c FROM attendance WHERE attendance_date BETWEEN ? AND ?",
            (start, end),
        ).fetchone()["c"]
        payment_sum = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM payments WHERE payment_date BETWEEN ? AND ?",
            (start, end),
        ).fetchone()["s"]
        row = db.execute(
            """
            SELECT
                COUNT(*) AS settled_count,
                COALESCE(SUM(total_due), 0) AS total_due,
                COALESCE(SUM(shopping_fee), 0) AS shopping_total,
                COALESCE(SUM(recharge_amount), 0) AS recharge_total,
                COALESCE(SUM(CASE WHEN ending_balance < 0 THEN 1 ELSE 0 END), 0) AS debt_count,
                COALESCE(SUM(CASE WHEN ending_balance < 0 THEN -ending_balance ELSE 0 END), 0) AS debt_total,
                COALESCE(SUM(CASE WHEN ending_balance > 0 THEN 1 ELSE 0 END), 0) AS positive_count,
                COALESCE(SUM(CASE WHEN ending_balance > 0 THEN ending_balance ELSE 0 END), 0) AS positive_total,
                COALESCE(SUM(unmarked_count), 0) AS unmarked_total
            FROM monthly_settlements
            WHERE month = ?
            """,
            (month,),
        ).fetchone()
    return {
        "month": month,
        "students_count": students_count,
        "attendance_records": attendance_count,
        "payment_sum": round_money(payment_sum),
        **{k: round_money(v) if k.endswith("_total") or k in {"total_due", "recharge_total"} else v for k, v in row.items()},
    }


def student_purchases_for_month(month: str, permanent_id: str | None = None) -> list[dict[str, Any]]:
    month = require_month(month)
    start, end = month_range(month)
    where = ["p.purchase_date BETWEEN ? AND ?"]
    args: list[Any] = [start, end]
    if permanent_id:
        where.append("p.permanent_id = ?")
        args.append(permanent_id)
    with db_conn() as db:
        return db.execute(
            f"""
            SELECT p.*, s.name, s.annual_id, s.grade, s.class_no
            FROM student_purchases p
            JOIN students s ON s.permanent_id = p.permanent_id
            WHERE {" AND ".join(where)}
            ORDER BY p.purchase_date, CAST(s.grade AS INTEGER), CAST(s.class_no AS INTEGER), s.seq_in_class, s.name
            """,
            args,
        ).fetchall()


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\r\n\t]+', "_", str(value or ""))
    value = re.sub(r"\s+", "_", value).strip("._ ")
    return value or "report"


def cn_status(value: str) -> str:
    return {
        "present": "\u5230",
        "leave": "\u8bf7\u5047",
        "absent": "\u672a\u5230",
        "unmarked": "\u672a\u70b9\u540d",
        "used": "\u4f7f\u7528",
        "not_used": "\u672a\u4f7f\u7528",
    }.get(value or "", value or "")


def current_rate_settings() -> dict[str, float]:
    settings = settings_map()
    return {
        "lunch_rate": round_money(settings.get("lunch_rate", 30)),
        "full_day_rate": round_money(settings.get("full_day_rate", 50)),
        "evening_only_rate": round_money(settings.get("evening_only_rate", settings.get("care_rate", 25))),
        "bed_monthly_fee": round_money(settings.get("bed_monthly_fee", settings.get("bed_daily_rate", 50))),
    }


def per_day_fee(lunch_status: str, care_status: str, rates: dict[str, float]) -> tuple[str, float]:
    if lunch_status == "present" and care_status == "present":
        return "\u5168\u5929\u6258\u7ba1", rates["full_day_rate"]
    if lunch_status == "present":
        return "\u5355\u72ec\u5348\u9910", rates["lunch_rate"]
    if care_status == "present":
        return "\u5355\u72ec\u665a\u6258", rates["evening_only_rate"]
    return "\u4e0d\u6536\u8d39", 0.0


def register_pdf_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for font_name, font_path in [
        ("YBS-CN", r"C:\Windows\Fonts\msyh.ttc"),
        ("YBS-CN", r"C:\Windows\Fonts\simhei.ttf"),
        ("YBS-CN", r"C:\Windows\Fonts\simsun.ttc"),
    ]:
        if Path(font_path).exists():
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path))
                return font_name
            except Exception:
                continue
    return "Helvetica"


def make_pdf(path: Path, title: str, subtitle: str, tables: list[tuple[str, list[list[Any]]]]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    path.parent.mkdir(parents=True, exist_ok=True)
    font = register_pdf_font()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="YBSTitle", fontName=font, fontSize=18, leading=24, spaceAfter=8))
    styles.add(ParagraphStyle(name="YBSSub", fontName=font, fontSize=10, leading=14, textColor=colors.HexColor("#475569")))
    styles.add(ParagraphStyle(name="YBSH2", fontName=font, fontSize=12, leading=16, spaceBefore=8, spaceAfter=6))
    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=title,
    )
    story: list[Any] = [Paragraph(title, styles["YBSTitle"]), Paragraph(subtitle, styles["YBSSub"]), Spacer(1, 6)]
    for section_title, rows in tables:
        story.append(Paragraph(section_title, styles["YBSH2"]))
        if not rows:
            rows = [["\u6682\u65e0\u6570\u636e"]]
        table = Table([[str(c) for c in row] for row in rows], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d4ed8")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 8))
    doc.build(story)


def upsert_report_archive(
    report_type: str,
    report_key: str,
    permanent_id: str | None,
    file_path: Path,
    actor: str,
) -> dict[str, Any]:
    with DB_LOCK:
        with db_conn() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT INTO report_archives(report_type, report_key, permanent_id, file_path, generated_by, generated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_type, report_key, permanent_id) DO UPDATE SET
                    file_path = excluded.file_path,
                    generated_by = excluded.generated_by,
                    generated_at = excluded.generated_at
                """,
                (report_type, report_key, permanent_id, str(file_path), actor, now_text()),
            )
            audit(db, actor, "generate report", "report", f"{report_type}:{report_key}:{permanent_id or ''}", None, {"file_path": str(file_path)})
            db.execute("COMMIT")
    return {"file_path": str(file_path), "report_type": report_type, "report_key": report_key, "permanent_id": permanent_id}


def daily_attendance_report(attendance_date: str, actor: str = "\u7cfb\u7edf") -> dict[str, Any]:
    attendance_date = require_date(attendance_date)
    rates = current_rate_settings()
    rows = attendance_for_class(attendance_date, "__all__", "__all__")
    table_rows = [["序号", "学生", "班级", "A午餐", "P下午", "收费项目", "当天费用", "购物", "购物金额", "床位", "事件提醒", "操作人", "更新时间"]]
    counts = {"lunch_only": 0, "full_day": 0, "evening_only": 0, "no_charge": 0, "unmarked": 0, "bed": 0, "event": 0, "shopping_total": 0.0}
    for idx, r in enumerate(rows, start=1):
        service, fee = per_day_fee(r["lunch_status"], r["care_status"], rates)
        if service == "\u5355\u72ec\u5348\u9910":
            counts["lunch_only"] += 1
        elif service == "\u5168\u5929\u6258\u7ba1":
            counts["full_day"] += 1
        elif service == "\u5355\u72ec\u665a\u6258":
            counts["evening_only"] += 1
        else:
            counts["no_charge"] += 1
        if r["lunch_status"] == "unmarked" or r["care_status"] == "unmarked":
            counts["unmarked"] += 1
        if r["bed_status"] == "used":
            counts["bed"] += 1
        if int(r.get("event_mark") or 0):
            counts["event"] += 1
        shopping_amount = round_money(r.get("shopping_amount"))
        counts["shopping_total"] = round_money(counts["shopping_total"] + shopping_amount)
        table_rows.append(
            [
                idx,
                r["name"],
                f"{r['grade']}-{r['class_no']}",
                cn_status(r["lunch_status"]),
                cn_status(r["care_status"]),
                service,
                f"{fee:.2f}",
                r.get("shopping_item") or "",
                f"{shopping_amount:.2f}" if shopping_amount else "",
                cn_status(r["bed_status"]),
                r.get("note") or "",
                r.get("operator") or "",
                r.get("updated_at") or "",
            ]
        )
    summary_rows = [
        ["项目", "数量/金额"],
        ["学生总数", len(rows)],
        ["单独午餐人数", counts["lunch_only"]],
        ["全天托管人数", counts["full_day"]],
        ["单独晚托人数", counts["evening_only"]],
        ["不收费人数", counts["no_charge"]],
        ["未点名异常", counts["unmarked"]],
        ["床位使用人数", counts["bed"]],
        ["事件标注人数", counts["event"]],
        ["购物金额合计", f"{counts['shopping_total']:.2f}"],
    ]
    month = attendance_date[:7]
    path = REPORTS_DIR / "\u65e5\u62a5" / month / f"{attendance_date}_\u6bcf\u65e5\u8003\u52e4\u62a5\u8868.pdf"
    make_pdf(
        path,
        f"\u4f18\u8d1d\u601d\u6821\u5916\u6258\u7ba1\u4e2d\u5fc3 - {attendance_date} \u6bcf\u65e5\u8003\u52e4\u62a5\u8868",
        f"\u751f\u6210\u65f6\u95f4\uff1a{now_text()}\u3000\u751f\u6210\u4eba\uff1a{actor}\u3000\u89c4\u5219\uff1a\u5355\u72ec\u5348\u9910{rates['lunch_rate']}/\u5929\uff0c\u5168\u5929\u6258\u7ba1{rates['full_day_rate']}/\u5929\uff0c\u5355\u72ec\u665a\u6258{rates['evening_only_rate']}/\u5929",
        [("\u6c47\u603b", summary_rows), ("\u660e\u7ec6", table_rows)],
    )
    return upsert_report_archive("daily", attendance_date, None, path, actor)


def student_attendance_report(month: str, permanent_id: str, actor: str = "\u7cfb\u7edf") -> dict[str, Any]:
    month = require_month(month)
    start, end = month_range(month)
    rates = current_rate_settings()
    with db_conn() as db:
        student = db.execute("SELECT * FROM students WHERE permanent_id = ?", (permanent_id,)).fetchone()
        if not student:
            raise ValueError("student not found")
        attendance = db.execute(
            """
            SELECT * FROM attendance
            WHERE permanent_id = ? AND attendance_date BETWEEN ? AND ?
            ORDER BY attendance_date
            """,
            (permanent_id, start, end),
        ).fetchall()
        purchases = db.execute(
            """
            SELECT * FROM student_purchases
            WHERE permanent_id = ? AND purchase_date BETWEEN ? AND ?
            ORDER BY purchase_date
            """,
            (permanent_id, start, end),
        ).fetchall()
    by_date = {r["attendance_date"]: r for r in attendance}
    purchases_by_date = {r["purchase_date"]: r for r in purchases}
    year, mon = [int(x) for x in month.split("-")]
    last_day = calendar.monthrange(year, mon)[1]
    rows = [["日期", "A午餐", "P下午", "收费项目", "当天费用", "购物", "购物金额", "床位", "事件提醒", "操作人", "更新时间"]]
    total_fee = shopping_total = 0.0
    lunch_present = care_present = lunch_recorded = care_recorded = event_count = 0
    for day in range(1, last_day + 1):
        d = f"{month}-{day:02d}"
        r = by_date.get(d) or {"lunch_status": "unmarked", "care_status": "unmarked", "bed_status": "not_used", "event_mark": 0, "note": "", "operator": "", "updated_at": ""}
        purchase = purchases_by_date.get(d) or {}
        service, fee = per_day_fee(r["lunch_status"], r["care_status"], rates)
        total_fee += fee
        purchase_amount = round_money(purchase.get("amount"))
        shopping_total = round_money(shopping_total + purchase_amount)
        if r["lunch_status"] == "present":
            lunch_present += 1
        if r["care_status"] == "present":
            care_present += 1
        if r["lunch_status"] != "unmarked":
            lunch_recorded += 1
        if r["care_status"] != "unmarked":
            care_recorded += 1
        if int(r.get("event_mark") or 0):
            event_count += 1
        rows.append([
            d,
            cn_status(r["lunch_status"]),
            cn_status(r["care_status"]),
            service,
            f"{fee:.2f}",
            purchase.get("item") or "",
            f"{purchase_amount:.2f}" if purchase_amount else "",
            cn_status(r["bed_status"]),
            r.get("note") or "",
            r.get("operator") or "",
            r.get("updated_at") or "",
        ])
    summary_rows = [
        ["项目", "数值"],
        ["学生", student["name"]],
        ["学年编号", student["annual_id"]],
        ["班级", f"{student['grade']}-{student['class_no']}"],
        ["午餐到/午餐记录", f"{lunch_present}/{lunch_recorded}"],
        ["晚托到/晚托记录", f"{care_present}/{care_recorded}"],
        ["事件标注次数", event_count],
        ["按日服务费用合计", f"{total_fee:.2f}"],
        ["购物金额合计", f"{shopping_total:.2f}"],
        ["服务+购物合计", f"{round_money(total_fee + shopping_total):.2f}"],
    ]
    path = REPORTS_DIR / "\u5b66\u751f\u51fa\u52e4\u62a5\u8868" / month / f"{safe_filename(student['name'])}_{student['annual_id']}_{month}_\u51fa\u52e4\u7f34\u8d39\u660e\u7ec6.pdf"
    make_pdf(
        path,
        f"\u4f18\u8d1d\u601d\u6821\u5916\u6258\u7ba1\u4e2d\u5fc3 - {student['name']} {month} \u51fa\u52e4\u7f34\u8d39\u660e\u7ec6",
        f"\u751f\u6210\u65f6\u95f4\uff1a{now_text()}\u3000\u751f\u6210\u4eba\uff1a{actor}",
        [("\u6c47\u603b", summary_rows), ("\u6bcf\u65e5\u660e\u7ec6", rows)],
    )
    return upsert_report_archive("student", month, permanent_id, path, actor)


def monthly_pdf_report(month: str, actor: str = "\u7cfb\u7edf") -> dict[str, Any]:
    month = require_month(month)
    if not settlement_rows(month):
        generate_settlement(month, actor)
    summary = settlement_summary(month)
    rows = settlement_rows(month)
    summary_rows = [
        ["项目", "数值"],
        ["月份", month],
        ["学生数", summary["students_count"]],
        ["已月结人数", summary["settled_count"]],
        ["本月应缴合计", f"{summary['total_due']:.2f}"],
        ["购物金额合计", f"{summary.get('shopping_total', 0):.2f}"],
        ["本月充值合计", f"{summary['recharge_total']:.2f}"],
        ["欠费人数", summary["debt_count"]],
        ["欠费合计", f"{summary['debt_total']:.2f}"],
        ["有余额人数", summary["positive_count"]],
        ["余额合计", f"{summary['positive_total']:.2f}"],
        ["未点名异常", summary["unmarked_total"]],
    ]
    detail_rows = [["学生", "班级", "期初", "充值", "单独午餐", "全天托管", "单独晚托", "床位", "购物", "应缴", "期末", "状态"]]
    for r in rows:
        detail_rows.append(
            [
                r["name"],
                f"{r['grade']}-{r['class_no']}",
                f"{r['opening_balance']:.2f}",
                f"{r['recharge_amount']:.2f}",
                f"{r['lunch_days']}天/{r['lunch_fee']:.2f}",
                f"{r.get('full_day_days', 0)}天/{r.get('full_day_fee', 0):.2f}",
                f"{r.get('evening_only_days', r['care_days'])}天/{r.get('evening_only_fee', r['care_fee']):.2f}",
                f"{r['bed_fee']:.2f}",
                f"{r.get('shopping_fee', 0):.2f}",
                f"{r['total_due']:.2f}",
                f"{r['ending_balance']:.2f}",
                r["balance_status"],
            ]
        )
    path = REPORTS_DIR / "\u6708\u62a5" / f"{month}_\u6708\u5ea6\u7ed3\u7b97\u62a5\u8868.pdf"
    make_pdf(path, f"\u4f18\u8d1d\u601d\u6821\u5916\u6258\u7ba1\u4e2d\u5fc3 - {month} \u6708\u5ea6\u7ed3\u7b97\u62a5\u8868", f"\u751f\u6210\u65f6\u95f4\uff1a{now_text()}\u3000\u751f\u6210\u4eba\uff1a{actor}", [("\u6c47\u603b", summary_rows), ("\u660e\u7ec6", detail_rows)])
    return upsert_report_archive("monthly", month, None, path, actor)


def cost_pdf_report(month: str, actor: str = "\u7cfb\u7edf") -> dict[str, Any]:
    month = require_month(month)
    data = list_costs({"month": [month]})
    summary = data["summary"]
    rows = data["rows"]
    summary_rows = [
        ["项目", "金额"],
        ["固定成本", f"{summary['fixed_total']:.2f}"],
        ["变动成本", f"{summary['variable_total']:.2f}"],
        ["人工成本", f"{summary['labor_total']:.2f}"],
        ["其他成本", f"{summary['other_total']:.2f}"],
        ["成本合计", f"{summary['grand_total']:.2f}"],
        ["记录条数", summary["record_count"]],
    ]
    detail_rows = [["日期", "类型", "项目", "金额", "备注", "登记人", "登记时间"]]
    for r in rows:
        detail_rows.append([r["cost_date"], r["cost_type_label"], r["item"], f"{r['amount']:.2f}", r["note"], r["operator"], r["created_at"]])
    path = REPORTS_DIR / "\u6210\u672c\u62a5\u8868" / f"{month}_\u6210\u672c\u8bb0\u5f55\u62a5\u8868.pdf"
    make_pdf(path, f"\u4f18\u8d1d\u601d\u6821\u5916\u6258\u7ba1\u4e2d\u5fc3 - {month} \u6210\u672c\u8bb0\u5f55\u62a5\u8868", f"\u751f\u6210\u65f6\u95f4\uff1a{now_text()}\u3000\u751f\u6210\u4eba\uff1a{actor}", [("\u6210\u672c\u6c47\u603b", summary_rows), ("\u6210\u672c\u660e\u7ec6", detail_rows)])
    return upsert_report_archive("cost", month, None, path, actor)


def dashboard(month: str) -> dict[str, Any]:
    return settlement_summary(require_month(month))


def col_name(index: int) -> str:
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def sheet_xml(rows: list[list[Any]], widths: list[int] | None = None) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>',
    ]
    if widths:
        lines.append("<cols>")
        for idx, width in enumerate(widths, start=1):
            lines.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
        lines.append("</cols>")
    lines.append("<sheetData>")
    for r_idx, row in enumerate(rows, start=1):
        lines.append(f'<row r="{r_idx}">')
        for c_idx, value in enumerate(row, start=1):
            ref = f"{col_name(c_idx)}{r_idx}"
            style = ' s="1"' if r_idx == 1 else ""
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                lines.append(f'<c r="{ref}"{style}><v>{round_money(value)}</v></c>')
            else:
                text = html.escape("" if value is None else str(value), quote=False)
                lines.append(f'<c r="{ref}" t="inlineStr"{style}><is><t>{text}</t></is></c>')
        lines.append("</row>")
    lines.append("</sheetData></worksheet>")
    return "\n".join(lines)


def make_xlsx(sheets: list[tuple[str, list[list[Any]], list[int] | None]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
"""
            + "\n".join(
                f'  <Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(sheets) + 1)
            )
            + "\n</Types>",
        )
        z.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        z.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
"""
            + "\n".join(
                f'    <sheet name="{html.escape(name[:31], quote=True)}" sheetId="{i}" r:id="rId{i}"/>'
                for i, (name, _, _) in enumerate(sheets, start=1)
            )
            + """
  </sheets>
</workbook>""",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
"""
            + "\n".join(
                f'  <Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(sheets) + 1)
            )
            + f'\n  <Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\n</Relationships>',
        )
        z.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Microsoft YaHei"/></font>
    <font><b/><sz val="11"/><name val="Microsoft YaHei"/><color rgb="FFFFFFFF"/></font>
  </fonts>
  <fills count="2">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF2563EB"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
  </cellXfs>
</styleSheet>""",
        )
        for i, (_, rows, widths) in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(rows, widths))
    return buffer.getvalue()


def settlement_workbook(month: str, pid: str | None = None) -> bytes:
    rows = settlement_rows(month)
    if pid:
        rows = [r for r in rows if r["permanent_id"] == pid]
    if not rows:
        generate_settlement(month)
        rows = settlement_rows(month)
        if pid:
            rows = [r for r in rows if r["permanent_id"] == pid]

    header = [
        "\u6708\u4efd",
        "\u6c38\u4e45\u5b66\u751fID",
        "\u5b66\u5e74\u7f16\u53f7",
        "\u59d3\u540d",
        "\u5e74\u7ea7",
        "\u73ed\u7ea7",
        "\u671f\u521d\u4f59\u989d",
        "\u672c\u6708\u5145\u503c",
        "\u5355\u72ec\u5348\u9910\u5929\u6570",
        "\u5355\u72ec\u5348\u9910\u5355\u4ef7",
        "\u5355\u72ec\u5348\u9910\u8d39\u7528",
        "\u5168\u5929\u6258\u7ba1\u5929\u6570",
        "\u5168\u5929\u6258\u7ba1\u5355\u4ef7",
        "\u5168\u5929\u6258\u7ba1\u8d39\u7528",
        "\u5355\u72ec\u665a\u6258\u5929\u6570",
        "\u5355\u72ec\u665a\u6258\u5355\u4ef7",
        "\u5355\u72ec\u665a\u6258\u8d39\u7528",
        "\u5e8a\u4f4d\u4f7f\u7528\u5929\u6570",
        "\u5e8a\u4f4d\u6708\u8d39",
        "\u5e8a\u4f4d\u8d39\u662f\u5426\u514d\u9664",
        "\u5e8a\u4f4d\u8d39\u7528",
        "\u8d2d\u7269\u91d1\u989d",
        "\u672c\u6708\u5e94\u7f34",
        "\u671f\u672b\u4f59\u989d",
        "\u72b6\u6001",
        "\u672a\u70b9\u540d\u5f02\u5e38\u6570",
        "\u751f\u6210\u65f6\u95f4",
    ]
    main_rows = [header]
    for r in rows:
        main_rows.append(
            [
                r["month"],
                r["permanent_id"],
                r["annual_id"],
                r["name"],
                r["grade"],
                r["class_no"],
                r["opening_balance"],
                r["recharge_amount"],
                r["lunch_days"],
                r["lunch_rate"],
                r["lunch_fee"],
                r.get("full_day_days", 0),
                r.get("full_day_rate", 50),
                r.get("full_day_fee", 0),
                r.get("evening_only_days", r["care_days"]),
                r.get("evening_only_rate", r["care_rate"]),
                r.get("evening_only_fee", r["care_fee"]),
                r["bed_days"],
                r.get("bed_monthly_fee", r["bed_rate"]),
                "\u662f" if int(r.get("bed_fee_exempt") or 0) else "\u5426",
                r["bed_fee"],
                r.get("shopping_fee", 0),
                r["total_due"],
                r["ending_balance"],
                r["balance_status"],
                r["unmarked_count"],
                r["generated_at"],
            ]
        )
    debt_rows = [header] + [row for row in main_rows[1:] if round_money(row[23]) < 0]
    detail_rows = [
        ["\u5b66\u751f\u7f34\u8d39\u660e\u7ec6"],
        [
            "\u6708\u4efd",
            "\u5b66\u751f",
            "\u5b66\u5e74\u7f16\u53f7",
            "\u4e0a\u6708/\u671f\u521d\u4f59\u989d",
            "\u672c\u6708\u5145\u503c",
            "\u8d39\u7528\u660e\u7ec6",
            "\u8d2d\u7269\u91d1\u989d",
            "\u672c\u6708\u5e94\u7f34",
            "\u671f\u672b\u4f59\u989d",
            "\u7f34\u8d39\u72b6\u6001",
        ],
    ]
    for r in rows:
        bed_charge_label = "\u514d\u9664" if int(r.get("bed_fee_exempt") or 0) else "\u6536\u53d6"
        fee_detail = (
            f"\u5355\u72ec\u5348\u9910 {r['lunch_days']} \u5929 \u00d7 {r['lunch_rate']} = {r['lunch_fee']}\uff1b"
            f"\u5168\u5929\u6258\u7ba1 {r.get('full_day_days', 0)} \u5929 \u00d7 {r.get('full_day_rate', 50)} = {r.get('full_day_fee', 0)}\uff1b"
            f"\u5355\u72ec\u665a\u6258 {r.get('evening_only_days', r['care_days'])} \u5929 \u00d7 {r.get('evening_only_rate', r['care_rate'])} = {r.get('evening_only_fee', r['care_fee'])}\uff1b"
            f"\u5e8a\u4f4d {r['bed_days']} \u5929\u4f7f\u7528\uff0c\u6708\u8d39 {r.get('bed_monthly_fee', r['bed_rate'])}\uff0c"
            f"{bed_charge_label} = {r['bed_fee']}\uff1b"
            f"\u8d2d\u7269 = {r.get('shopping_fee', 0)}"
        )
        detail_rows.append(
            [
                r["month"],
                r["name"],
                r["annual_id"],
                r["opening_balance"],
                r["recharge_amount"],
                fee_detail,
                r.get("shopping_fee", 0),
                r["total_due"],
                r["ending_balance"],
                r["balance_status"],
            ]
        )
    summary = settlement_summary(month)
    summary_rows = [
        ["\u9879\u76ee", "\u6570\u503c"],
        ["\u6708\u4efd", month],
        ["\u5b66\u751f\u6570", summary["students_count"]],
        ["\u5df2\u6708\u7ed3\u4eba\u6570", summary["settled_count"]],
        ["\u672c\u6708\u5e94\u7f34\u5408\u8ba1", summary["total_due"]],
        ["\u8d2d\u7269\u91d1\u989d\u5408\u8ba1", summary.get("shopping_total", 0)],
        ["\u672c\u6708\u5145\u503c\u5408\u8ba1", summary["recharge_total"]],
        ["\u6b20\u8d39\u4eba\u6570", summary["debt_count"]],
        ["\u6b20\u8d39\u5408\u8ba1", summary["debt_total"]],
        ["\u6709\u4f59\u989d\u4eba\u6570", summary["positive_count"]],
        ["\u4f59\u989d\u5408\u8ba1", summary["positive_total"]],
        ["\u672a\u70b9\u540d\u5f02\u5e38\u6570", summary["unmarked_total"]],
    ]
    return make_xlsx(
        [
            ("\u6708\u5ea6\u6c47\u603b", summary_rows, [18, 18]),
            ("\u6708\u5ea6\u603b\u8868", main_rows, [12, 16, 18, 12, 8, 8, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 14, 20]),
            ("\u6b20\u8d39\u540d\u5355", debt_rows, [12, 16, 18, 12, 8, 8, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 14, 20]),
            ("\u5b66\u751f\u7f34\u8d39\u660e\u7ec6", detail_rows, [14, 12, 18, 14, 12, 48, 12, 12, 12, 12]),
        ]
    )


class AppHandler(BaseHTTPRequestHandler):
    server_version = "JYAttendanceBilling/0.1"

    def parse_query(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    def cookie_token(self) -> str | None:
        cookie_header = self.headers.get("Cookie") or ""
        for item in cookie_header.split(";"):
            if "=" not in item:
                continue
            key, value = item.strip().split("=", 1)
            if key == SESSION_COOKIE:
                return urllib.parse.unquote(value)
        return None

    def current_user(self) -> dict[str, Any] | None:
        return user_for_session(self.cookie_token())

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def send_json(self, data: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def send_bytes(self, data: bytes, content_type: str, filename: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            quoted = urllib.parse.quote(filename)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, exc: Exception, status: int = 400) -> None:
        self.send_json({"ok": False, "error": str(exc)}, status)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path, params = self.parse_query()
        try:
            if path == "/api/health":
                self.send_json({"ok": True, "time": now_text(), "db": str(DB_PATH)})
            elif path == "/api/me":
                self.send_json({"ok": True, "user": self.current_user()})
            elif path == "/api/network":
                require_login_user(self.current_user())
                port = int(os.environ.get("PORT") or os.environ.get("JY_PORT", "8766"))
                urls = [f"http://{ip}:{port}" for ip in local_ip_addresses()]
                self.send_json({"ok": True, "urls": urls, "port": port})
            elif path == "/api/students":
                require_admin(self.current_user())
                self.send_json({"ok": True, "students": list_students(params)})
            elif path == "/api/classes":
                require_login_user(self.current_user())
                self.send_json({"ok": True, "classes": list_classes()})
            elif path == "/api/settings":
                require_admin(self.current_user())
                self.send_json({"ok": True, "settings": settings_map()})
            elif path == "/api/attendance":
                require_login_user(self.current_user())
                attendance_date = (params.get("date") or [today_text()])[0]
                grade = (params.get("grade") or [""])[0]
                class_no = (params.get("class_no") or [""])[0]
                self.send_json({"ok": True, "records": attendance_for_class(attendance_date, grade, class_no)})
            elif path == "/api/payments":
                require_admin(self.current_user())
                self.send_json({"ok": True, "payments": list_payments(params)})
            elif path == "/api/costs":
                require_admin(self.current_user())
                self.send_json({"ok": True, **list_costs(params)})
            elif path == "/api/dashboard":
                require_admin(self.current_user())
                month = (params.get("month") or [today_text()[:7]])[0]
                self.send_json({"ok": True, "dashboard": dashboard(month)})
            elif path == "/api/settlements":
                require_admin(self.current_user())
                month = (params.get("month") or [today_text()[:7]])[0]
                self.send_json({"ok": True, "summary": settlement_summary(month), "rows": settlement_rows(month)})
            elif path == "/api/reports/daily":
                require_login_user(self.current_user())
                attendance_date = (params.get("date") or [today_text()])[0]
                result = daily_attendance_report(attendance_date, user_display(self.current_user()))
                if (params.get("download") or [""])[0] == "1":
                    file_path = Path(result["file_path"])
                    self.send_bytes(file_path.read_bytes(), "application/pdf", file_path.name)
                else:
                    self.send_json({"ok": True, "report": result})
            elif path == "/api/reports/student":
                require_admin(self.current_user())
                month = (params.get("month") or [today_text()[:7]])[0]
                pid = (params.get("permanent_id") or [""])[0]
                if not pid:
                    raise ValueError("missing permanent_id")
                result = student_attendance_report(month, pid, user_display(self.current_user()))
                if (params.get("download") or [""])[0] == "1":
                    file_path = Path(result["file_path"])
                    self.send_bytes(file_path.read_bytes(), "application/pdf", file_path.name)
                else:
                    self.send_json({"ok": True, "report": result})
            elif path == "/api/reports/monthly":
                require_admin(self.current_user())
                month = (params.get("month") or [today_text()[:7]])[0]
                result = monthly_pdf_report(month, user_display(self.current_user()))
                if (params.get("download") or [""])[0] == "1":
                    file_path = Path(result["file_path"])
                    self.send_bytes(file_path.read_bytes(), "application/pdf", file_path.name)
                else:
                    self.send_json({"ok": True, "report": result})
            elif path == "/api/reports/costs":
                require_admin(self.current_user())
                month = (params.get("month") or [today_text()[:7]])[0]
                result = cost_pdf_report(month, user_display(self.current_user()))
                if (params.get("download") or [""])[0] == "1":
                    file_path = Path(result["file_path"])
                    self.send_bytes(file_path.read_bytes(), "application/pdf", file_path.name)
                else:
                    self.send_json({"ok": True, "report": result})
            elif path == "/api/users":
                require_admin(self.current_user())
                self.send_json({"ok": True, "users": list_users()})
            elif path == "/api/export/monthly":
                require_admin(self.current_user())
                month = (params.get("month") or [today_text()[:7]])[0]
                data = settlement_workbook(month)
                self.send_bytes(
                    data,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    f"\u4f18\u8d1d\u601d\u6821\u5916\u6258\u7ba1\u4e2d\u5fc3_{month}_\u6708\u7ed3\u4e0e\u6b20\u8d39\u660e\u7ec6.xlsx",
                )
            elif path == "/api/export/student":
                require_admin(self.current_user())
                month = (params.get("month") or [today_text()[:7]])[0]
                pid = (params.get("permanent_id") or [""])[0]
                if not pid:
                    raise ValueError("missing permanent_id")
                data = settlement_workbook(month, pid)
                self.send_bytes(
                    data,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    f"\u4f18\u8d1d\u601d\u6821\u5916\u6258\u7ba1\u4e2d\u5fc3_{month}_{pid}_\u7f34\u8d39\u660e\u7ec6.xlsx",
                )
            else:
                self.serve_static(path)
        except HttpError as exc:
            self.send_error_json(exc, exc.status)
        except Exception as exc:
            self.send_error_json(exc)

    def do_POST(self) -> None:
        path, _ = self.parse_query()
        try:
            payload = self.read_json()
            if path == "/api/login":
                result = login_user(str(payload.get("username") or ""), str(payload.get("pin") or ""))
                cookie = (
                    f"{SESSION_COOKIE}={urllib.parse.quote(result['token'])}; "
                    f"Path=/; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; SameSite=Lax"
                )
                self.send_json({"ok": True, "user": result["user"]}, headers={"Set-Cookie": cookie})
                return
            if path == "/api/logout":
                user = self.current_user()
                logout_token(self.cookie_token(), user_display(user))
                clear_cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
                self.send_json({"ok": True}, headers={"Set-Cookie": clear_cookie})
                return

            user = self.current_user()
            actor = user_display(user)
            if path == "/api/seed/import-students":
                require_admin(user)
                self.send_json({"ok": True, "result": seed_students(actor)})
            elif path == "/api/admin/import-students":
                require_admin(user)
                records = payload.get("records") or []
                self.send_json({"ok": True, "result": import_student_records(records, actor, "api-upload")})
            elif path == "/api/settings":
                require_admin(user)
                self.send_json({"ok": True, "settings": update_settings(payload, actor)})
            elif path == "/api/attendance/bulk":
                require_login_user(user)
                payload["operator"] = actor
                self.send_json({"ok": True, "result": save_attendance_bulk(payload)})
            elif path == "/api/payments":
                require_admin(user)
                payload["operator"] = actor
                self.send_json({"ok": True, "payment": create_payment(payload)})
            elif path == "/api/costs":
                require_admin(user)
                self.send_json({"ok": True, "cost": create_cost_record(payload, actor)})
            elif path == "/api/settlements/generate":
                require_admin(user)
                month = str(payload.get("month") or today_text()[:7])
                result = generate_settlement(month, actor)
                report = None
                report_warning = None
                try:
                    report = monthly_pdf_report(month, actor)
                except Exception as exc:
                    report_warning = str(exc)
                self.send_json({"ok": True, "result": result | {"monthly_report": report, "report_warning": report_warning}})
            elif path == "/api/reports/daily":
                require_login_user(user)
                attendance_date = str(payload.get("date") or today_text())
                self.send_json({"ok": True, "report": daily_attendance_report(attendance_date, actor)})
            elif path == "/api/reports/student":
                require_admin(user)
                month = str(payload.get("month") or today_text()[:7])
                pid = str(payload.get("permanent_id") or "")
                if not pid:
                    raise ValueError("missing permanent_id")
                self.send_json({"ok": True, "report": student_attendance_report(month, pid, actor)})
            elif path == "/api/reports/monthly":
                require_admin(user)
                month = str(payload.get("month") or today_text()[:7])
                self.send_json({"ok": True, "report": monthly_pdf_report(month, actor)})
            elif path == "/api/reports/costs":
                require_admin(user)
                month = str(payload.get("month") or today_text()[:7])
                self.send_json({"ok": True, "report": cost_pdf_report(month, actor)})
            elif path == "/api/users":
                require_admin(user)
                self.send_json({"ok": True, "user": upsert_user(payload, actor)})
            else:
                self.send_error_json(ValueError("unknown api endpoint"), 404)
        except HttpError as exc:
            self.send_error_json(exc, exc.status)
        except Exception as exc:
            self.send_error_json(exc)

    def serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        if path.startswith("/static/"):
            target = STATIC_DIR / path.replace("/static/", "", 1)
        else:
            target = STATIC_DIR / path.lstrip("/")
        target = target.resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists() or not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (now_text(), fmt % args))


def local_ip_addresses() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def main() -> None:
    execute_script()
    host = os.environ.get("JY_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT") or os.environ.get("JY_PORT", "8766"))
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), AppHandler)
    server.daemon_threads = True
    print("\u4f18\u8d1d\u601d\u6821\u5916\u6258\u7ba1\u4e2d\u5fc3\u8003\u52e4\u6536\u8d39\u7cfb\u7edf\u5df2\u542f\u52a8")
    print(f"\u6570\u636e\u5e93\uff1a{DB_PATH}")
    print("\u8bbf\u95ee\u5730\u5740\uff1a")
    for ip in local_ip_addresses():
        print(f"  http://{ip}:{port}")
    print("\u6309 Ctrl+C \u505c\u6b62\u3002")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\u5df2\u505c\u6b62\u3002")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
