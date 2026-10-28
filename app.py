"""Self-contained standard-library WSGI prototype for student grade lookup.

Run with: python3 app.py
This is a local demonstration, not a production-ready student information system.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
import statistics
import time
from collections import defaultdict
from http import cookies
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)
DB_PATH = Path(os.environ.get("DATABASE_PATH", str(INSTANCE_DIR / "dashboard_demo.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SECRET_KEY = os.environ.get("APP_SECRET_KEY", "local-demo-only-change-this-secret").encode("utf-8")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
COOKIE_NAME = "tk_grade_session"
SESSION_LIFETIME = 30 * 60
MAX_BODY_SIZE = 8192
LOGIN_WINDOW_SECONDS = 300
MAX_LOGIN_ATTEMPTS = 5
_login_attempts: dict[str, list[float]] = defaultdict(list)

COURSE_DEFINITIONS = [
    {"course_code": "TKJ201", "course_name": "Jaringan Komputer", "section": "A", "credits": 3, "means": 76, "target_scores": [85, 90, 78, 82, 88]},
    {"course_code": "TKA202", "course_name": "Struktur Data", "section": "A", "credits": 3, "means": 79, "target_scores": [78, 88, 82, 79, 85]},
    {"course_code": "TKD203", "course_name": "Sistem Digital", "section": "B", "credits": 2, "means": 77, "target_scores": [92, 85, 90, 88, 91]},
]
ASSESSMENTS = [("Kuis", "Kuis", 15), ("Tugas", "Tugas", 20), ("Praktikum", "Praktikum", 25), ("UTS", "Ujian", 20), ("UAS", "Ujian", 20)]


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def code_hash(code: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(code.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode().rstrip("=") + "$" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def verify_code(code: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
        expected = base64.urlsafe_b64decode(digest_b64 + "=" * (-len(digest_b64) % 4))
        actual = hashlib.scrypt(code.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = connect_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nim TEXT NOT NULL UNIQUE,
                access_code_hash TEXT,
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS offerings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_code TEXT NOT NULL,
                course_name TEXT NOT NULL,
                term TEXT NOT NULL,
                section TEXT NOT NULL,
                credits INTEGER NOT NULL CHECK (credits > 0),
                UNIQUE (course_code, term, section)
            );
            CREATE TABLE IF NOT EXISTS enrollments (
                offering_id INTEGER NOT NULL REFERENCES offerings(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                PRIMARY KEY (offering_id, student_id)
            );
            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offering_id INTEGER NOT NULL REFERENCES offerings(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                weight_percent REAL NOT NULL CHECK (weight_percent >= 0 AND weight_percent <= 100),
                is_published INTEGER NOT NULL DEFAULT 1 CHECK (is_published IN (0, 1)),
                UNIQUE (offering_id, title)
            );
            CREATE TABLE IF NOT EXISTS grades (
                assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (assessment_id, student_id)
            );
            CREATE INDEX IF NOT EXISTS idx_enrollments_student ON enrollments(student_id);
            CREATE INDEX IF NOT EXISTS idx_assessments_offering ON assessments(offering_id);
            CREATE INDEX IF NOT EXISTS idx_grades_student ON grades(student_id);
        """)
        if conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]:
            return

        demo_nim, demo_code = "DEMO-240001", "TK-DEMO-2026"
        conn.execute("INSERT INTO students (nim, access_code_hash) VALUES (?, ?)", (demo_nim, code_hash(demo_code)))
        demo_id = conn.execute("SELECT id FROM students WHERE nim = ?", (demo_nim,)).fetchone()[0]
        import random
        rng = random.Random(20261028)
        term = "2026/2027 Ganjil"

        for course in COURSE_DEFINITIONS:
            cur = conn.execute(
                "INSERT INTO offerings (course_code, course_name, term, section, credits) VALUES (?, ?, ?, ?, ?)",
                (course["course_code"], course["course_name"], term, course["section"], course["credits"]),
            )
            offering_id = cur.lastrowid
            assessment_ids = []
            for title, category, weight in ASSESSMENTS:
                c = conn.execute(
                    "INSERT INTO assessments (offering_id, title, category, weight_percent, is_published) VALUES (?, ?, ?, ?, 1)",
                    (offering_id, title, category, weight),
                )
                assessment_ids.append(c.lastrowid)

            cohort_ids = [demo_id]
            for i in range(1, 18):
                fake_nim = f"SYN-{course['course_code']}-{i:03d}"
                c = conn.execute("INSERT INTO students (nim, access_code_hash) VALUES (?, NULL)", (fake_nim,))
                cohort_ids.append(c.lastrowid)
            for student_id in cohort_ids:
                conn.execute("INSERT INTO enrollments (offering_id, student_id) VALUES (?, ?)", (offering_id, student_id))
                for idx, assessment_id in enumerate(assessment_ids):
                    if student_id == demo_id:
                        score = course["target_scores"][idx]
                    else:
                        score = round(max(35, min(100, rng.gauss(course["means"], 11))), 2)
                    conn.execute("INSERT INTO grades (assessment_id, student_id, score) VALUES (?, ?, ?)", (assessment_id, student_id, score))
        conn.commit()
    finally:
        conn.close()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_session(data: dict[str, Any]) -> str:
    payload = b64url(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(SECRET_KEY, payload.encode("ascii"), hashlib.sha256).hexdigest()
    return payload + "." + signature


def read_session(environ: dict[str, Any]) -> dict[str, Any] | None:
    jar = cookies.SimpleCookie()
    try:
        jar.load(environ.get("HTTP_COOKIE", ""))
        token = jar[COOKIE_NAME].value if COOKIE_NAME in jar else ""
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(SECRET_KEY, payload.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        value = json.loads(unb64url(payload))
        if not isinstance(value, dict) or int(value.get("exp", 0)) < int(time.time()):
            return None
        if not isinstance(value.get("csrf"), str):
            return None
        return value
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def cookie_header(token: str, max_age: int = SESSION_LIFETIME) -> tuple[str, str]:
    suffix = "; Secure" if COOKIE_SECURE else ""
    return ("Set-Cookie", f"{COOKIE_NAME}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{suffix}")


def clear_cookie_header() -> tuple[str, str]:
    suffix = "; Secure" if COOKIE_SECURE else ""
    return ("Set-Cookie", f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{suffix}")


def new_session(student_id: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"csrf": secrets.token_urlsafe(32), "exp": int(time.time()) + SESSION_LIFETIME}
    if student_id is not None:
        payload["sid"] = int(student_id)
    return payload


def respond(start_response: Callable, status: str, body: str | bytes, extra_headers: list[tuple[str, str]] | None = None, content_type: str = "text/html; charset=utf-8"):
    data = body.encode("utf-8") if isinstance(body, str) else body
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(data))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; form-action 'self'; base-uri 'self'; frame-ancestors 'none'"),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    start_response(status, headers)
    return [data]


def base_page(title: str, content: str, logged_in: bool = False, csrf: str = "") -> str:
    logout = f'''<form method="post" action="/logout" class="logout-form"><input type="hidden" name="csrf_token" value="{esc(csrf)}"><button type="submit" class="button button-ghost button-small">Keluar</button></form>''' if logged_in else '<span class="topbar-tag"><span class="status-dot"></span> Prototipe demo</span>'
    return f'''<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#101d3a"><title>{esc(title)}</title><link rel="stylesheet" href="/static/styles.css"></head><body><header class="topbar"><a class="brand" href="/" aria-label="Dashboard Nilai TK"><span class="brand-mark">TK</span><span class="brand-copy"><strong>Nilai TK</strong><small>Portal akademik mahasiswa</small></span></a>{logout}</header><main class="page-shell">{content}</main><footer class="site-footer"><span>Prototipe pembelajaran · Data seluruhnya sintetis</span><span>Teknik Komputer</span></footer></body></html>'''


def render_login(csrf: str, error: str | None = None) -> str:
    error_html = f'<div class="alert alert-error" role="alert">{esc(error)}</div>' if error else ""
    content = f'''
<section class="login-layout">
  <div class="intro-panel">
    <div class="eyebrow"><span class="eyebrow-line"></span> PORTAL NILAI MAHASISWA</div>
    <h1>Kenali progresmu.<br><span>Rencanakan langkah berikutnya.</span></h1>
    <p class="intro-lede">Lihat nilai yang telah dipublikasikan, perkembangan akademik, dan gambaran performa kelas dalam satu tempat.</p>
    <div class="feature-list">
      <div class="feature-item"><span class="feature-icon">↗</span><span><strong>Perkembangan nilai</strong><small>Rincian tiap komponen penilaian</small></span></div>
      <div class="feature-item"><span class="feature-icon">▥</span><span><strong>Perbandingan kelas</strong><small>Rata-rata, median, dan distribusi nilai</small></span></div>
      <div class="feature-item"><span class="feature-icon">◈</span><span><strong>Akses pribadi</strong><small>Hanya nilai akun yang terverifikasi</small></span></div>
    </div>
    <div class="privacy-note"><span class="privacy-icon">⌑</span><p><strong>Privasi dijaga</strong><br>Statistik ditampilkan secara agregat. Identitas dan nilai individual mahasiswa lain tidak ditampilkan.</p></div>
  </div>
  <section class="login-card" aria-labelledby="login-title">
    <div class="card-overline">AKSES MAHASISWA</div><h2 id="login-title">Lihat nilai kamu</h2><p class="muted">Masukkan NIM dan kode akses pribadi yang diberikan dosen.</p>
    {error_html}
    <form action="/lookup" method="post" class="login-form"><input type="hidden" name="csrf_token" value="{esc(csrf)}"><label for="nim">NIM</label><div class="input-wrap"><span class="input-symbol">▤</span><input id="nim" name="nim" type="text" placeholder="Masukkan NIM" autocomplete="username" autocapitalize="characters" required maxlength="40"></div><label for="access_code">Kode akses pribadi</label><div class="input-wrap"><span class="input-symbol">⌑</span><input id="access_code" name="access_code" type="password" placeholder="Masukkan kode akses" autocomplete="current-password" required maxlength="200"></div><button class="button button-primary button-full" type="submit">Lihat dashboard <span aria-hidden="true">→</span></button></form>
    <div class="login-separator"><span></span><small>AKSES AMAN</small><span></span></div><div class="login-footnote"><span class="lock-icon">▣</span><span>Jangan bagikan kode akses. Jika kode hilang atau diduga bocor, hubungi dosen untuk penggantian.</span></div>
    <div class="demo-credentials"><div class="demo-label"><span class="demo-dot"></span> AKUN SIMULASI</div><div class="credential-row"><span>NIM demo</span><code>DEMO-240001</code></div><div class="credential-row"><span>Kode akses</span><code>TK-DEMO-2026</code></div><p>Gunakan hanya untuk mencoba prototipe ini. Bukan data mahasiswa nyata.</p></div>
  </section>
</section>'''
    return base_page("Lihat Nilai · Dashboard Nilai TK", content)


def fmt(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def calculate_course(db: sqlite3.Connection, offering: sqlite3.Row, student_id: int) -> dict[str, Any]:
    assessment_rows = db.execute(
        """SELECT a.id, a.title, a.category, a.weight_percent, a.is_published, g.score
           FROM assessments a LEFT JOIN grades g ON g.assessment_id = a.id AND g.student_id = ?
           WHERE a.offering_id = ? ORDER BY a.id""",
        (student_id, offering["id"]),
    ).fetchall()
    components = []
    weighted_sum = available_weight = 0.0
    for row in assessment_rows:
        score = float(row["score"]) if row["is_published"] and row["score"] is not None else None
        contribution = None
        if score is not None:
            contribution = score * float(row["weight_percent"]) / 100.0
            weighted_sum += contribution
            available_weight += float(row["weight_percent"])
        components.append({"title": row["title"], "category": row["category"], "weight": float(row["weight_percent"]), "score": score, "contribution": contribution})
    provisional = weighted_sum * 100.0 / available_weight if available_weight else None

    all_rows = db.execute(
        """SELECT s.id AS student_id, a.weight_percent, g.score
           FROM enrollments e JOIN students s ON s.id = e.student_id
           JOIN assessments a ON a.offering_id = e.offering_id AND a.is_published = 1
           JOIN grades g ON g.assessment_id = a.id AND g.student_id = s.id
           WHERE e.offering_id = ? ORDER BY s.id, a.id""",
        (offering["id"],),
    ).fetchall()
    per_student: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for row in all_rows:
        per_student[int(row["student_id"])].append((float(row["score"]), float(row["weight_percent"])))
    finals = []
    for scores in per_student.values():
        total_weight = sum(weight for _, weight in scores)
        if total_weight > 0:
            finals.append(sum(score * weight / 100.0 for score, weight in scores) * 100.0 / total_weight)
    finals.sort()
    avg = statistics.mean(finals) if finals else None
    med = statistics.median(finals) if finals else None
    percentile = sum(1 for score in finals if provisional is not None and score < provisional) / len(finals) * 100 if finals and provisional is not None else None
    bins = [(0, 59.999, "0–59"), (60, 69.999, "60–69"), (70, 79.999, "70–79"), (80, 89.999, "80–89"), (90, 100, "90–100")]
    distribution = []
    for low, high, label in bins:
        count = sum(1 for score in finals if low <= score <= high)
        distribution.append({"label": label, "count": count, "percent": round(count / len(finals) * 100) if finals else 0})
    return {
        "id": offering["id"], "course_code": offering["course_code"], "course_name": offering["course_name"],
        "term": offering["term"], "section": offering["section"], "credits": offering["credits"],
        "components": components, "provisional": None if provisional is None else round(provisional, 2),
        "coverage": round(available_weight, 2), "class_average": None if avg is None else round(avg, 2),
        "class_median": None if med is None else round(med, 2),
        "difference": None if avg is None or provisional is None else round(provisional - avg, 2),
        "percentile": None if percentile is None else round(percentile), "sample_count": len(finals), "distribution": distribution,
    }


def render_dashboard(nim: str, csrf: str, courses: list[dict[str, Any]]) -> str:
    values = [c["provisional"] for c in courses if c["provisional"] is not None]
    overall = statistics.mean(values) if values else None
    course_cards = []
    for course in courses:
        diff = course["difference"]
        diff_cls = "positive" if diff is not None and diff >= 0 else "negative"
        diff_text = f"{diff:+.2f}" if diff is not None else "—"
        component_rows = []
        for item in course["components"]:
            if item["score"] is None:
                score_html = '<span class="not-published">Belum tersedia</span>'
                contribution = "—"
            else:
                score_html = f'<strong>{item["score"]:.2f}</strong>'
                contribution = f'{item["contribution"]:.2f}'
            component_rows.append(f'<tr><td><span class="assessment-name">{esc(item["title"])}</span><small class="table-subtitle">{esc(item["category"])}</small></td><td>{item["weight"]:.0f}%</td><td>{score_html}</td><td>{contribution}</td></tr>')
        bars = []
        for item in course["distribution"]:
            highlight = " bar-fill-highlight" if item["label"] == "80–89" else ""
            bars.append(f'<div class="bar-row"><span class="bar-label">{esc(item["label"])}</span><div class="bar-track"><div class="bar-fill{highlight}" style="width: {item["percent"]}%"></div></div><span class="bar-count">{item["count"]}</span></div>')
        course_cards.append(f'''
<article class="course-card"><div class="course-card-header"><div class="course-title-wrap"><div class="course-code">{esc(course['course_code'])} <span>·</span> {course['credits']} SKS</div><h3>{esc(course['course_name'])}</h3><div class="course-meta"><span>{esc(course['term'])}</span><span class="meta-dot">•</span><span>Kelas {esc(course['section'])}</span></div></div><div class="course-score"><small>Nilai sementara</small><strong>{fmt(course['provisional'])}</strong><span>Cakupan {course['coverage']:.0f}% bobot</span></div></div>
<div class="comparison-grid"><div class="comparison-stat yours"><small>Nilai kamu</small><strong>{fmt(course['provisional'])}</strong><span>Nilai sementara</span></div><div class="comparison-stat"><small>Rata-rata kelas</small><strong>{fmt(course['class_average'])}</strong><span>{course['sample_count']} data siswa</span></div><div class="comparison-stat"><small>Median kelas</small><strong>{fmt(course['class_median'])}</strong><span>Nilai tengah kelas</span></div><div class="comparison-stat"><small>Selisih dari rata-rata</small><strong class="{diff_cls}">{diff_text}</strong><span>Dalam skala 0–100</span></div></div>
<div class="course-detail-grid"><div class="assessment-panel"><div class="panel-heading"><div><h4>Rincian penilaian</h4><p>Nilai dan bobot yang telah dipublikasikan</p></div><span class="tiny-tag">{len(course['components'])} komponen</span></div><div class="table-scroll"><table><thead><tr><th>Komponen</th><th>Bobot</th><th>Nilai</th><th>Kontribusi</th></tr></thead><tbody>{''.join(component_rows)}</tbody></table></div></div>
<div class="distribution-panel"><div class="panel-heading"><div><h4>Distribusi kelas</h4><p>Rentang nilai akhir sementara</p></div><span class="chart-icon">▥</span></div><div class="distribution-chart" role="img" aria-label="Distribusi nilai akhir sementara berdasarkan rentang nilai">{''.join(bars)}</div><div class="percentile-note"><span class="percentile-symbol">↗</span><span>Nilaimu lebih tinggi dari sekitar <strong>{course['percentile'] if course['percentile'] is not None else '—'}%</strong> nilai dalam kelompok pembanding.</span></div><p class="chart-footnote">Statistik memakai {course['sample_count']} mahasiswa di kelas yang sama. Tidak ada nilai individu teman yang ditampilkan.</p></div></div></article>''')
    content = f'''
<section class="dashboard-header"><div><div class="eyebrow"><span class="eyebrow-line"></span> RINGKASAN AKADEMIK</div><h1>Dashboard nilai</h1><p class="muted">Selamat datang. Berikut ringkasan nilai yang sudah dipublikasikan untuk akun demo kamu.</p></div><div class="student-chip"><span class="avatar">M</span><span><small>Mahasiswa demo</small><strong>{esc(nim)}</strong></span><span class="verified">✓</span></div></section>
<section class="summary-grid" aria-label="Ringkasan nilai"><article class="summary-card summary-primary"><div class="summary-top"><span>Rata-rata lintas mata kuliah</span><span class="summary-icon">◒</span></div><div class="summary-value">{fmt(overall)}<small>/ 100</small></div><div class="summary-bottom"><span class="mini-indicator"></span>Rata-rata sederhana dari nilai sementara yang tersedia</div></article><article class="summary-card"><div class="summary-top"><span>Mata kuliah</span><span class="summary-icon purple">▦</span></div><div class="summary-value">{len(courses)}<small> kelas</small></div><div class="summary-bottom">Semester 2026/2027 Ganjil</div></article><article class="summary-card"><div class="summary-top"><span>Status data</span><span class="summary-icon green">✓</span></div><div class="summary-value status-value">Aktif</div><div class="summary-bottom"><span class="mini-indicator green-dot"></span>Menampilkan nilai yang dipublikasikan</div></article></section>
<div class="section-heading"><div><h2>Nilai per mata kuliah</h2><p>Bandingkan nilai pribadi dengan statistik agregat kelas.</p></div><span class="data-badge"><span></span> Data simulasi</span></div>
<section class="course-list">{''.join(course_cards)}</section><div class="bottom-note"><span class="note-icon">i</span><p><strong>Perlu diperhatikan:</strong> nilai sementara dihitung dari komponen yang telah dipublikasikan dan dinormalisasi terhadap bobot yang tersedia. Perbandingan ini bersifat informatif, bukan nilai resmi akhir mata kuliah.</p></div>'''
    return base_page("Dashboard Mahasiswa · Nilai TK", content, logged_in=True, csrf=csrf)


def render_error(status_code: int, message: str) -> str:
    content = f'<section class="error-panel"><div class="error-code">{status_code}</div><h1>Permintaan tidak dapat diproses</h1><p>{esc(message)}</p><a class="button button-primary" href="/">Kembali ke halaman utama</a></section>'
    return base_page("Permintaan tidak dapat diproses · Nilai TK", content)


def login_allowed(client_key: str) -> bool:
    now = time.time()
    _login_attempts[client_key] = [t for t in _login_attempts[client_key] if now - t < LOGIN_WINDOW_SECONDS]
    return len(_login_attempts[client_key]) < MAX_LOGIN_ATTEMPTS


def render_page_for_student(student_id: int, csrf: str) -> str | None:
    db = connect_db()
    try:
        student = db.execute("SELECT nim FROM students WHERE id = ? AND is_active = 1", (student_id,)).fetchone()
        if student is None:
            return None
        offerings = db.execute(
            """SELECT o.* FROM offerings o JOIN enrollments e ON e.offering_id = o.id
               WHERE e.student_id = ? ORDER BY o.course_name""",
            (student_id,),
        ).fetchall()
        courses = [calculate_course(db, offering, student_id) for offering in offerings]
        return render_dashboard(student["nim"], csrf, courses)
    finally:
        db.close()


def app(environ: dict[str, Any], start_response: Callable):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")
    session_data = read_session(environ)

    if path == "/static/styles.css" and method == "GET":
        css_path = BASE_DIR / "static" / "styles.css"
        return respond(start_response, "200 OK", css_path.read_bytes(), content_type="text/css; charset=utf-8")

    if method == "GET" and path == "/":
        if session_data and session_data.get("sid"):
            body = render_page_for_student(int(session_data["sid"]), session_data["csrf"])
            if body is not None:
                return respond(start_response, "200 OK", body)
        session_data = session_data or new_session()
        # Refresh the short-lived signed anonymous session that carries the CSRF token.
        session_data["exp"] = int(time.time()) + SESSION_LIFETIME
        headers = [cookie_header(make_session(session_data))]
        return respond(start_response, "200 OK", render_login(session_data["csrf"]), headers)

    if method == "GET" and path == "/dashboard":
        if not session_data or not session_data.get("sid"):
            return respond(start_response, "303 See Other", "", [("Location", "/")])
        body = render_page_for_student(int(session_data["sid"]), session_data["csrf"])
        if body is None:
            return respond(start_response, "303 See Other", "", [("Location", "/"), clear_cookie_header()])
        return respond(start_response, "200 OK", body)

    if method == "POST" and path in ("/lookup", "/logout"):
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError:
            length = MAX_BODY_SIZE + 1
        if length < 0 or length > MAX_BODY_SIZE:
            return respond(start_response, "413 Payload Too Large", render_error(413, "Permintaan terlalu besar."))
        body_bytes = environ["wsgi.input"].read(length)
        form = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)
        get_form = lambda key: form.get(key, [""])[0]
        submitted_csrf = get_form("csrf_token")
        stored_csrf = session_data.get("csrf", "") if session_data else ""
        if not submitted_csrf or not stored_csrf or not hmac.compare_digest(submitted_csrf, stored_csrf):
            return respond(start_response, "400 Bad Request", render_error(400, "Permintaan tidak valid. Muat ulang halaman lalu coba lagi."))

        if path == "/logout":
            return respond(start_response, "303 See Other", "", [("Location", "/"), clear_cookie_header()])

        client_key = environ.get("REMOTE_ADDR", "unknown")
        if not login_allowed(client_key):
            return respond(start_response, "429 Too Many Requests", render_login(stored_csrf, "Terlalu banyak percobaan. Tunggu beberapa menit sebelum mencoba lagi."))
        nim = get_form("nim").strip().upper()
        access_code = get_form("access_code")
        db = connect_db()
        try:
            row = db.execute("SELECT id, access_code_hash, is_active FROM students WHERE nim = ?", (nim,)).fetchone() if nim and len(nim) <= 40 and len(access_code) <= 200 else None
            valid = bool(row and row["is_active"] and verify_code(access_code, row["access_code_hash"]))
            if not valid:
                _login_attempts[client_key].append(time.time())
                return respond(start_response, "401 Unauthorized", render_login(stored_csrf, "NIM atau kode akses tidak valid."))
            new_data = new_session(int(row["id"]))
            return respond(start_response, "303 See Other", "", [("Location", "/dashboard"), cookie_header(make_session(new_data))])
        finally:
            db.close()

    return respond(start_response, "404 Not Found", render_error(404, "Halaman yang diminta tidak ditemukan."))


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"Dashboard Nilai TK prototipe tersedia di http://127.0.0.1:{port}")
    print("Kredensial simulasi: NIM DEMO-240001 | kode TK-DEMO-2026")
    with make_server("127.0.0.1", port, app) as server:
        server.serve_forever()
