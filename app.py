import os
import re
from dotenv import load_dotenv
import secrets
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)
import requests
from urllib.parse import urlencode

from flask import Flask, request, jsonify, render_template, session, redirect
from werkzeug.security import check_password_hash, generate_password_hash
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from ml.efficientnet import predict_image
from db import init_db, seed_if_empty, db, get_setting, set_setting


# --- Google Maps API Key sanitize (fix hidden/BOM/UTF-16 issues that become %0E%0B... in URL) ---
def _sanitize_maps_key(raw: str) -> str:
    if not raw:
        return ""
    raw = ("" + raw).strip()
    raw = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    return raw

def get_google_maps_key() -> str:
    # 1) env var
    key = _sanitize_maps_key(os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("GOOGLE_MAPS_KEY") or "")
    if key.startswith("AIza") and len(key) >= 25:
        return key

    # 2) fallback: parse .env bytes (handles UTF-16 / BOM / hidden chars)
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        b = env_path.read_bytes()
        for enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
            try:
                s = b.decode(enc, errors="ignore")
            except Exception:
                continue
            m = re.search(r"AIza[0-9A-Za-z\-_]{20,}", s)
            if m:
                return m.group(0)

    return key
# --------------------------------------------------------------------

app = Flask(__name__)

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

CORS(app)


# Session secret (set FLASK_SECRET_KEY in production)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
# If serving behind HTTPS (e.g. ngrok), make cookies compatible.
_public_base_url = (os.environ.get("PUBLIC_BASE_URL") or "").strip().lower()
if _public_base_url.startswith("https://"):
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

# Flask 3.x removed before_first_request; run DB init once via before_request
_db_initialized = False

@app.before_request
def init_once():
    global _db_initialized
    if not _db_initialized:
        init_db(reset_if_mismatch=True)
        seed_if_empty()
        _db_initialized = True


# ====================================================
# Google OAuth2 (real redirect to provider)
# Env vars required:
#   GOOGLE_CLIENT_ID
#   GOOGLE_CLIENT_SECRET
# Optional:
#   GOOGLE_REDIRECT_URI (default http://localhost:5000/auth/google/callback)
# ====================================================

GOOGLE_CLIENT_ID = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()

# --- Optional: load Google OAuth client from JSON (client_secret_*.json) ---
# If GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set in env, we will try to read them
# from a Google OAuth client JSON file (the one you download from Google Cloud Console).
def _load_google_client_from_json():
    global GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_AUTH_URI, GOOGLE_TOKEN_URI

    # Allow explicit path via env
    candidates = []
    for k in ("GOOGLE_OAUTH_CLIENT_JSON", "GOOGLE_CLIENT_SECRET_FILE", "GOOGLE_CLIENT_JSON"):
        v = (os.environ.get(k) or "").strip()
        if v:
            candidates.append(Path(v))
    # Auto-detect in project folder (same folder as this app.py)
    here = Path(__file__).resolve().parent
    candidates += sorted(here.glob("client_secret_*.json"))

    for p in candidates:
        try:
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            cfg = data.get("web") or data.get("installed") or {}
            cid = (cfg.get("client_id") or "").strip()
            csec = (cfg.get("client_secret") or "").strip()
            auth_uri = (cfg.get("auth_uri") or "https://accounts.google.com/o/oauth2/v2/auth").strip()
            token_uri = (cfg.get("token_uri") or "https://oauth2.googleapis.com/token").strip()
            if cid and csec:
                GOOGLE_CLIENT_ID = GOOGLE_CLIENT_ID or cid
                GOOGLE_CLIENT_SECRET = GOOGLE_CLIENT_SECRET or csec
                GOOGLE_AUTH_URI = auth_uri
                GOOGLE_TOKEN_URI = token_uri
                print(f"[OAUTH] Loaded client from JSON: {p.name} cid_suffix={cid[-10:]}")
                return
        except Exception as e:
            print(f"[OAUTH] Failed reading {p}: {e}")

# Defaults (will be overwritten by JSON loader if available)
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URI = "https://openidconnect.googleapis.com/v1/userinfo"

# If env didn't provide ID/SECRET, try JSON
if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
    _load_google_client_from_json()

# Prefer explicit GOOGLE_REDIRECT_URI; also accept GOOGLE_REDIRECT_URL for compatibility.
# If PUBLIC_BASE_URL is set (e.g. https://xxxx.ngrok-free.dev) we derive redirect from it.
_public_base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
GOOGLE_REDIRECT_URI = (
    (os.environ.get("GOOGLE_REDIRECT_URI") or "").strip()
    or (os.environ.get("GOOGLE_REDIRECT_URL") or "").strip()
    or ((_public_base + "/auth/google/callback") if _public_base else "http://127.0.0.1:5000/auth/google/callback")
).strip()

def _require_google_oauth_config():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return False
    # ✅ ถ้าใช้ ngrok/https ต้องมี PUBLIC_BASE_URL หรือ GOOGLE_REDIRECT_URI
    if (os.environ.get("PUBLIC_BASE_URL") or "").strip().startswith("https://") and not GOOGLE_REDIRECT_URI:
        return False
    return True


@app.get("/api/me")
def api_me():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in": True,
        "user": {
            "id": uid,
            "name": session.get("user_name"),
            "email": session.get("user_email"),
            "avatar": session.get("user_avatar"),
            "provider": session.get("user_provider", "google"),
            "role": session.get("user_role", "user"),
            "hasPasswordLogin": bool(session.get("user_has_password_login", False)),
        }
    })

@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.get("/auth/google")
def auth_google():
    if not _require_google_oauth_config():
        return "Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET env vars", 500

    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
        "include_granted_scopes": "true",
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))

@app.get("/auth/google/callback")
def auth_google_callback():
    if not _require_google_oauth_config():
        return "Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET env vars", 500

    state = request.args.get("state", "")
    code = request.args.get("code", "")
    if not code or not state or state != session.get("oauth_state"):
        return "OAuth state mismatch", 400

    # Exchange code for token
    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if token_resp.status_code != 200:
        return f"Token exchange failed: {token_resp.text}", 400

    token = token_resp.json()
    access_token = token.get("access_token")
    if not access_token:
        return "No access token", 400

    # Userinfo
    info_resp = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if info_resp.status_code != 200:
        return f"Userinfo failed: {info_resp.text}", 400

    info = info_resp.json()
    sub = info.get("sub")
    email = info.get("email")
    name = info.get("name") or info.get("given_name") or (email.split("@")[0] if email else "Google User")
    picture = info.get("picture")

    if not sub:
        return "Missing user sub", 400

    # Upsert to DB
    with db() as conn:
        conn.execute(
            """INSERT INTO users (provider, provider_sub, email, display_name, avatar_url)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(provider, provider_sub) DO UPDATE SET
                 email=excluded.email,
                 display_name=excluded.display_name,
                 avatar_url=excluded.avatar_url
            """,
            ("google", sub, email, name, picture),
        )
        row = conn.execute(
            "SELECT id AS user_id, display_name AS name, email, avatar_url FROM users WHERE provider=? AND provider_sub=?",
            ("google", sub),
        ).fetchone()

    session["user_id"] = row["user_id"]
    session["user_name"] = row["name"]
    session["user_email"] = row["email"]
    session["user_avatar"] = row["avatar_url"]
    session["user_provider"] = "google"
    session["user_role"] = "user"
    session["user_has_password_login"] = False
    session.pop("oauth_state", None)

    return redirect("/#oauth=google")

@app.get("/")
def index():
    # Google Maps key is injected from env so the same build can run on localhost / ngrok / domain.
    # Set GOOGLE_MAPS_API_KEY to enable the map.
    gmaps_key = get_google_maps_key()
    return render_template("index.html", gmaps_key=gmaps_key, site_name="LannaVeg")



@app.post("/api/register")
def api_register():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    display_name = str(data.get("name") or "").strip()[:80]

    if not email or not password:
        return jsonify({"ok": False, "error": "email_password_required"}), 400

    pw_hash = generate_password_hash(password)

    with db() as conn:
        # ensure password_hash column exists (light migration)
        try:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "password_hash" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        except Exception:
            pass

        exists = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if exists:
            return jsonify({"ok": False, "error": "email_exists"}), 409

        conn.execute(
            "INSERT INTO users(provider, provider_sub, display_name, email, password_hash) VALUES(?,?,?,?,?)",
            ("local", email, display_name or email.split("@")[0], email, pw_hash),
        )
        row = conn.execute("SELECT id AS user_id, display_name AS name, email FROM users WHERE email=?", (email,)).fetchone()
        conn.commit()

    session["user_id"] = row["user_id"]
    session["user_name"] = row["name"]
    session["user_email"] = row["email"]
    session["user_provider"] = "local"
    session["user_role"] = "user"
    session["user_has_password_login"] = True
    return jsonify({"ok": True})

@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")

    if not email or not password:
        return jsonify({"ok": False, "error": "email_password_required"}), 400

    with db() as conn:
        try:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "password_hash" not in cols:
                return jsonify({"ok": False, "error": "no_local_auth"}), 400
        except Exception:
            return jsonify({"ok": False, "error": "db_error"}), 500

        row = conn.execute(
            "SELECT id AS user_id, display_name AS name, email, password_hash FROM users WHERE email=?",
            (email,),
        ).fetchone()

    if not row or not row["password_hash"] or not check_password_hash(row["password_hash"], password):
        return jsonify({"ok": False, "error": "invalid_credentials"}), 401

    session["user_id"] = row["user_id"]
    session["user_name"] = row["name"]
    session["user_email"] = row["email"]
    session["user_provider"] = "local"
    session["user_role"] = "user"
    session["user_has_password_login"] = True
    return jsonify({"ok": True})

# =============================
# User / Profile
# =============================

def _require_user_id():
    uid = session.get("user_id")
    if not uid:
        return None, (jsonify({"ok": False, "error": "login_required"}), 401)
    return uid, None


@app.get("/api/profile")
def api_profile_get():
    uid, err = _require_user_id()
    if err:
        return err
    with db() as conn:
        row = conn.execute(
            "SELECT id, display_name, email, avatar_url, provider FROM users WHERE id=?",
            (uid,),
        ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "profile": dict(row)})


@app.post("/api/profile")
def api_profile_update():
    uid, err = _require_user_id()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    display_name = str(data.get("display_name") or "").strip()[:80]
    avatar_url = str(data.get("avatar_url") or "").strip()[:500]
    with db() as conn:
        conn.execute(
            "UPDATE users SET display_name=COALESCE(?, display_name), avatar_url=COALESCE(?, avatar_url) WHERE id=?",
            (display_name if display_name else None, avatar_url if avatar_url else None, uid),
        )
        conn.commit()
    # Keep session in sync
    if display_name:
        session["user_name"] = display_name
    if avatar_url:
        session["user_avatar"] = avatar_url
    return jsonify({"ok": True})


# =============================
# Map markers + Reviews (server-synced)
# =============================

@app.get("/api/markers")
def api_markers_list():
    """List markers with aggregated rating. Optional filters: veg_key, province."""
    veg_key = str(request.args.get("veg_key") or "").strip()
    province = str(request.args.get("province") or "").strip()

    q = """
      SELECT m.id, m.veg_key, v.thai_name, v.en_name, v.other_names,
             m.place_name, m.province, m.lat, m.lon, m.user_id, m.created_at,
             ROUND(AVG(r.rating), 2) AS avg_rating,
             COUNT(r.id) AS review_count
      FROM markers m
      JOIN vegetables v ON v.class_key = m.veg_key
      LEFT JOIN reviews r ON r.marker_id = m.id
      WHERE 1=1
    """
    params = []
    if veg_key:
        q += " AND m.veg_key=?"
        params.append(veg_key)
    if province:
        q += " AND m.province=?"
        params.append(province)
    q += " GROUP BY m.id ORDER BY m.created_at DESC"

    with db() as conn:
        rows = conn.execute(q, params).fetchall()
    return jsonify({"ok": True, "markers": [dict(r) for r in rows]})


@app.post("/api/markers")
def api_markers_create():
    """Create marker. Requires login."""
    uid, err = _require_user_id()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    veg_key = str(data.get("veg_key") or "").strip()
    place_name = str(data.get("place_name") or "").strip()[:120]
    province = str(data.get("province") or "").strip()[:80]
    lat = data.get("lat")
    lon = data.get("lon")
    if not veg_key or lat is None or lon is None:
        return jsonify({"ok": False, "error": "bad_request"}), 400
    with db() as conn:
        # Ensure veg exists
        vr = conn.execute("SELECT 1 FROM vegetables WHERE class_key=?", (veg_key,)).fetchone()
        if not vr:
            return jsonify({"ok": False, "error": "veg_not_found"}), 404
        cur = conn.execute(
            "INSERT INTO markers(veg_key,user_id,place_name,province,lat,lon) VALUES(?,?,?,?,?,?)",
            (veg_key, uid, place_name, province, float(lat), float(lon)),
        )
        conn.commit()
        marker_id = cur.lastrowid
    return jsonify({"ok": True, "marker_id": marker_id})


@app.get("/api/reviews")
def api_reviews_list():
    """List reviews (all). Filters: veg_key, province, min_rating."""
    veg_key = str(request.args.get("veg_key") or "").strip()
    province = str(request.args.get("province") or "").strip()
    try:
        min_rating = int(request.args.get("min_rating") or 0)
    except Exception:
        min_rating = 0

    q = """
      SELECT r.id, r.marker_id, r.rating, r.comment, r.created_at,
             m.veg_key, v.thai_name, m.place_name, m.province,
             u.display_name AS user_name
      FROM reviews r
      JOIN markers m ON m.id = r.marker_id
      JOIN vegetables v ON v.class_key = m.veg_key
      JOIN users u ON u.id = r.user_id
      WHERE 1=1
    """
    params = []
    if veg_key:
        q += " AND m.veg_key=?"
        params.append(veg_key)
    if province:
        q += " AND m.province=?"
        params.append(province)
    if min_rating:
        q += " AND r.rating>=?"
        params.append(min_rating)
    q += " ORDER BY r.created_at DESC LIMIT 500"
    with db() as conn:
        rows = conn.execute(q, params).fetchall()
    return jsonify({"ok": True, "reviews": [dict(r) for r in rows]})


@app.post("/api/reviews")
def api_reviews_create():
    """Create review for a marker. Requires login."""
    uid, err = _require_user_id()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    marker_id = data.get("marker_id")
    rating = int(data.get("rating") or 5)
    rating = max(1, min(5, rating))
    comment = str(data.get("comment") or "").strip()[:800]
    if not marker_id:
        return jsonify({"ok": False, "error": "bad_request"}), 400
    with db() as conn:
        mr = conn.execute("SELECT user_id FROM markers WHERE id=?", (marker_id,)).fetchone()
        if not mr:
            return jsonify({"ok": False, "error": "marker_not_found"}), 404
        # Only allow the marker owner to submit the first review right after pin confirmation.
        if int(mr["user_id"]) != int(uid):
            return jsonify({"ok": False, "error": "only_owner_can_review"}), 403
        conn.execute(
            "INSERT INTO reviews(marker_id,user_id,rating,comment) VALUES(?,?,?,?)",
            (marker_id, uid, rating, comment),
        )
        conn.commit()
    return jsonify({"ok": True})


# =============================
# Helpers
# =============================

def require_user():
    uid = session.get("user_id")
    if not uid:
        return None, (jsonify({"ok": False, "error": "login_required"}), 401)
    return uid, None


# =============================
# Admin (moved to backend)
# =============================
# Env vars:
#   ADMIN_EMAILS: comma separated
#   ADMIN_PASSWORD: password
#   ADMIN_CODE: optional second factor code

_DEFAULT_ADMIN_EMAILS = (
    "lannVeg_addmin01@gmail.com,"
    "lannVeg_addmin02@gmail.com,"
    "lannVeg_addmin03@gmail.com"
)

def _admin_emails():
    raw = os.environ.get("ADMIN_EMAILS", _DEFAULT_ADMIN_EMAILS)
    return {e.strip().lower() for e in raw.split(",") if e.strip()}

def _admin_password_hash():
    pwd = os.environ.get("ADMIN_PASSWORD", "admin1234")
    # Hash once per process start (ok for dev)
    return generate_password_hash(pwd)

ADMIN_PASSWORD_HASH = _admin_password_hash()

def is_admin_session():
    return session.get("role") == "admin"

@app.get("/admin")
def admin_page():
    # Simple admin page (requires login)
    if not is_admin_session():
        return render_template("admin.html", logged_in=False)
    return render_template("admin.html", logged_in=True)

@app.post("/api/admin/login")
def admin_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    code = str(data.get("code") or "")

    if email not in _admin_emails():
        return jsonify({"ok": False, "error": "not_allowed"}), 403

    if not check_password_hash(ADMIN_PASSWORD_HASH, password):
        return jsonify({"ok": False, "error": "bad_password"}), 401

    expected_code = os.environ.get("ADMIN_CODE", "")
    if expected_code and code != expected_code:
        return jsonify({"ok": False, "error": "bad_code"}), 401

    session["role"] = "admin"
    session["email"] = email
    return jsonify({"ok": True, "role": "admin", "email": email})

@app.post("/api/admin/logout")
def admin_logout():
    session.pop("role", None)
    session.pop("email", None)
    return jsonify({"ok": True})

@app.get("/api/admin/status")
def admin_status():
    return jsonify({"ok": True, "role": session.get("role"), "email": session.get("email")})




@app.get("/api/admin/config")
def admin_config_get():
    return jsonify({
        "public_url": get_setting("public_url", ""),
        "note": "Store any admin-only settings here (extend as needed).",
    })


@app.post("/api/admin/config")
def admin_config_set():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(force=True, silent=True) or {}
    public_url = str(data.get("public_url", "")).strip()
    set_setting("public_url", public_url)
    return jsonify({"ok": True, "public_url": public_url})

@app.get("/health")
def health():
    return jsonify({"status": "ok"})

@app.get("/api/vegs")
def api_vegs():
    with db() as conn:
        rows = conn.execute(
            "SELECT class_key AS veg_key, thai_name, en_name, other_names, scientific_name, nutrition, cooking, COALESCE(group_name,'ผักพื้นเมือง') AS group_name FROM vegetables ORDER BY thai_name"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/api/vegs/<veg_key>")
def api_veg_detail(veg_key):
    with db() as conn:
        row = conn.execute(
            "SELECT class_key AS veg_key, thai_name, en_name, other_names, scientific_name, nutrition, cooking, COALESCE(group_name,'ผักพื้นเมือง') AS group_name FROM vegetables WHERE class_key=?",
            (veg_key,),
        ).fetchone()
    if not row:
        return jsonify({"error": "not_found"}), 404
    return jsonify(dict(row))

@app.post("/predict")
def predict():
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400

    pred = predict_image(request.files["file"])
    ck = pred.get("classKey")
    if ck:
        try:
            with db() as conn:
                row = conn.execute(
                    "SELECT class_key AS veg_key, thai_name, en_name, other_names, scientific_name, nutrition, cooking, COALESCE(group_name,'ผักพื้นเมือง') AS group_name FROM vegetables WHERE class_key=?",
                    (ck,),
                ).fetchone()
            if row:
                veg = dict(row)
                veg["benefits"] = veg.get("nutrition")
                # Search link requirement: allow click-to-search (real link)
                q = veg.get("thai_name") or veg.get("en_name") or ck
                veg["search_url"] = f"https://www.google.com/search?q={requests.utils.quote(str(q))}"
                pred["veg"] = veg
                # UI requirement: show Thai name first, then other names
                other = " • ".join([x for x in [veg.get("en_name"), veg.get("other_names")] if x])
                pred["label"] = (veg.get("thai_name") or ck) + (f" — {other}" if other else "")
        except Exception:
            app.logger.exception("Failed to attach veg detail")

    # Frontend uses only top-1 (label + veg detail)
    return jsonify(pred)


# =============================
# Google Maps: Real markers + reviews (DB)
# =============================

@app.get("/api/markers/public", endpoint="api_markers_public")
def api_markers_public():
    """Public list for displaying pins on Google Maps."""
    veg_key = (request.args.get("veg_key") or "").strip()
    province = (request.args.get("province") or "").strip()

    sql = """
      SELECT m.id, m.veg_key, v.thai_name AS veg_name, m.place_name, m.province, m.lat, m.lon,
             m.user_id AS owner_id,
             (SELECT AVG(r.rating) FROM reviews r WHERE r.marker_id=m.id) AS avg_rating,
             (SELECT COUNT(1) FROM reviews r WHERE r.marker_id=m.id) AS review_count
      FROM markers m
      JOIN vegetables v ON v.class_key=m.veg_key
      WHERE 1=1
    """
    args = []
    if veg_key:
        sql += " AND m.veg_key=?"
        args.append(veg_key)
    if province:
        sql += " AND m.province LIKE ?"
        args.append(f"%{province}%")
    sql += " ORDER BY m.created_at DESC"

    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)