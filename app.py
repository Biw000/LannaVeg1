import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify, redirect, render_template, request, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from db import init_db, seed_if_empty, db
from ml.efficientnet import predict_image


BASE_DIR = Path(__file__).resolve().parent

# ✅ Render ใช้ env vars อยู่แล้ว (ไม่ต้องอ่าน .env)
# โหลด .env เฉพาะตอนรันในเครื่อง (optional)
try:
    from dotenv import load_dotenv
    if (os.environ.get("RENDER") or "").lower() != "true":
        load_dotenv(BASE_DIR / ".env", override=True)
except Exception:
    pass


def _sanitize_maps_key(raw: str) -> str:
    if not raw:
        return ""
    raw = ("" + raw).strip()
    raw = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    return raw


def get_google_maps_key() -> str:
    # ✅ ใช้ชื่อเดียวกับ Render: MAPS_API_KEY
    key = _sanitize_maps_key(
        os.getenv("MAPS_API_KEY") or
        os.getenv("GOOGLE_MAPS_API_KEY") or
        os.getenv("GOOGLE_MAPS_KEY") or
        ""
    )
    if key.startswith("AIza") and len(key) >= 25:
        return key
    return ""


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app)

# ✅ ใช้ SECRET_KEY ตาม Render
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# --- init db once ---
_db_initialized = False


@app.before_request
def init_once():
    global _db_initialized
    if not _db_initialized:
        init_db(reset_if_mismatch=True)
        seed_if_empty()
        _db_initialized = True


# ======================
# OAuth config
# ======================
GOOGLE_CLIENT_ID = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()

PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
GOOGLE_REDIRECT_URI = (os.environ.get("GOOGLE_REDIRECT_URI") or "").strip() or (
    f"{PUBLIC_BASE_URL}/auth/google/callback" if PUBLIC_BASE_URL else "http://127.0.0.1:5000/auth/google/callback"
)

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URI = "https://openidconnect.googleapis.com/v1/userinfo"


def _require_google_oauth_config() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI)


@app.get("/")
def index():
    return render_template(
        "index.html",
        gmaps_key=get_google_maps_key(),
        site_name="LannaVeg",
    )


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
        }
    })


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.get("/auth/google")
def auth_google():
    if not _require_google_oauth_config():
        return "Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / PUBLIC_BASE_URL (or GOOGLE_REDIRECT_URI)", 500

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
    }
    return redirect(GOOGLE_AUTH_URI + "?" + urlencode(params))


@app.get("/auth/google/callback")
def auth_google_callback():
    if not _require_google_oauth_config():
        return "Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / redirect", 500

    state = request.args.get("state", "")
    code = request.args.get("code", "")
    if not code or not state or state != session.get("oauth_state"):
        return "OAuth state mismatch", 400

    token_resp = requests.post(
        GOOGLE_TOKEN_URI,
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

    access_token = (token_resp.json() or {}).get("access_token")
    if not access_token:
        return "No access token", 400

    info_resp = requests.get(
        GOOGLE_USERINFO_URI,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if info_resp.status_code != 200:
        return f"Userinfo failed: {info_resp.text}", 400

    info = info_resp.json() or {}
    sub = info.get("sub")
    email = info.get("email")
    name = info.get("name") or info.get("given_name") or (email.split("@")[0] if email else "Google User")
    picture = info.get("picture")

    if not sub:
        return "Missing user sub", 400

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
    session.pop("oauth_state", None)

    return redirect("/#oauth=google")


# ======================
# Veg APIs
# ======================
@app.get("/api/vegs")
def api_vegs():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
              class_key AS veg_key,
              thai_name, en_name, other_names,
              scientific_name,
              nutrition,
              cooking,
              notes,
              COALESCE(group_name,'ผักพื้นเมือง') AS group_name
            FROM vegetables
            ORDER BY thai_name
            """
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/vegs/<veg_key>")
def api_veg_detail(veg_key: str):
    with db() as conn:
        row = conn.execute(
            """
            SELECT
              class_key AS veg_key,
              thai_name, en_name, other_names,
              scientific_name,
              nutrition,
              cooking,
              notes,
              COALESCE(group_name,'ผักพื้นเมือง') AS group_name
            FROM vegetables
            WHERE class_key=?
            """,
            (veg_key,),
        ).fetchone()
    if not row:
        return jsonify({"error": "not_found"}), 404
    return jsonify(dict(row))


# ======================
# Predict
# ======================
@app.post("/predict")
def predict():
    if "file" not in request.files:
        return jsonify({"error": "no_file"}), 400

    pred = predict_image(request.files["file"])  # returns dict
    ck = (pred.get("classKey") or pred.get("class_key") or "").strip()

    # ✅ compatibility fields เผื่อหน้าเว็บอ่านชื่อเก่า
    pred["class_key"] = pred.get("class_key") or (ck if ck else None)
    pred["veg_key"] = pred.get("veg_key") or (ck if ck else None)
    pred["predicted_class"] = pred.get("predicted_class") or pred.get("label")

    if ck:
        with db() as conn:
            # ✅ แก้หลัก: ค้นหาได้หลายแบบ (veg_key / scientific_name / thai_name / en_name)
            row = conn.execute(
                """
                SELECT
                  class_key AS veg_key,
                  thai_name, en_name, other_names,
                  scientific_name,
                  nutrition,
                  cooking,
                  notes,
                  COALESCE(group_name,'ผักพื้นเมือง') AS group_name
                FROM vegetables
                WHERE class_key = ?
                   OR lower(scientific_name) = lower(?)
                   OR lower(thai_name) = lower(?)
                   OR lower(en_name) = lower(?)
                LIMIT 1
                """,
                (ck, ck, ck, ck),
            ).fetchone()

        if row:
            veg = dict(row)

            # ✅ ให้ชื่อ key ที่ UI ชอบใช้
            veg["benefits"] = veg.get("nutrition") or "-"
            veg["menus"] = veg.get("cooking") or "-"
            veg["notes"] = veg.get("notes") or "-"
            q = veg.get("thai_name") or veg.get("en_name") or ck
            veg["search_url"] = f"https://www.google.com/search?q={requests.utils.quote(str(q))}"

            pred["veg"] = veg
            pred["veg_key"] = veg.get("veg_key")  # ให้ตรงกับ list /api/vegs

            # ✅ label ให้เป็นชื่อไทยก่อน
            other = " • ".join([x for x in [veg.get("en_name"), veg.get("other_names")] if x])
            pred["label"] = (veg.get("thai_name") or ck) + (f" — {other}" if other else "")
            pred["predicted_class"] = pred["label"]

    return jsonify(pred)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
