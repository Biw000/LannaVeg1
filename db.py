from __future__ import annotations
import sqlite3
from pathlib import Path
from contextlib import contextmanager

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data.sqlite3"

# ✅ แก้/เติมข้อมูลตรงนี้ให้ครบ (นี่คือต้นทางจริง)
VEG_SEED = [
    {
        "class_key": "Tupistra albiflora",
        "thai_name": "นางแลว",
        "en_name": "Tupistra",
        "other_names": "",
        "scientific_name": "Tupistra albiflora",
        "group_name": "ผักพื้นบ้าน",
        "nutrition": "สรรพคุณ: ช่วย... (ใส่ของจริงได้เลย)",
        "cooking": "ลวก/ต้ม/แกง/ผัด",
        "notes": "หมายเหตุ: ...",
    },
    {
        "class_key": "Azadirachta indica",
        "thai_name": "สะเดา",
        "en_name": "Neem",
        "other_names": "",
        "scientific_name": "Azadirachta indica",
        "group_name": "ผักพื้นเมือง",
        "nutrition": "สรรพคุณ: ...",
        "cooking": "ลวกจิ้มน้ำปลาหวาน",
        "notes": "",
    },
    # ✅ เติมเพิ่มให้ครบ 6 คลาสของคุณได้เลย
]

SCHEMA_VERSION = 1

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db(reset_if_mismatch: bool = True):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS _meta(
            k TEXT PRIMARY KEY,
            v TEXT
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_sub TEXT NOT NULL,
            email TEXT,
            display_name TEXT,
            avatar_url TEXT,
            UNIQUE(provider, provider_sub)
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS vegetables(
            class_key TEXT PRIMARY KEY,
            thai_name TEXT,
            en_name TEXT,
            other_names TEXT,
            scientific_name TEXT,
            group_name TEXT,
            nutrition TEXT,
            cooking TEXT,
            notes TEXT
        )""")

        # schema version
        row = conn.execute("SELECT v FROM _meta WHERE k='schema_version'").fetchone()
        if not row:
            conn.execute("INSERT INTO _meta(k,v) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
            conn.commit()
        else:
            if reset_if_mismatch and row["v"] != str(SCHEMA_VERSION):
                # reset vegetables only (keep users)
                conn.execute("DELETE FROM vegetables")
                conn.execute("UPDATE _meta SET v=? WHERE k='schema_version'", (str(SCHEMA_VERSION),))
                conn.commit()

def seed_if_empty():
    with db() as conn:
        row = conn.execute("SELECT COUNT(1) AS n FROM vegetables").fetchone()
        if row and int(row["n"]) > 0:
            return

        for v in VEG_SEED:
            conn.execute("""
                INSERT INTO vegetables(
                    class_key, thai_name, en_name, other_names, scientific_name,
                    group_name, nutrition, cooking, notes
                ) VALUES(?,?,?,?,?,?,?,?,?)
            """, (
                v.get("class_key",""),
                v.get("thai_name",""),
                v.get("en_name",""),
                v.get("other_names",""),
                v.get("scientific_name",""),
                v.get("group_name",""),
                v.get("nutrition",""),
                v.get("cooking",""),
                v.get("notes",""),
            ))
        conn.commit()
