import os, sqlite3, time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data.sqlite3"))

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _table_cols(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}
    except Exception:
        return set()

def _schema_ok(conn) -> bool:
    need = {
        "users": {"id","provider","provider_sub","display_name","email","avatar_url","created_at"},
        "vegetables": {"class_key","thai_name","en_name","other_names","scientific_name","nutrition","cooking"},
        "markers": {"id","veg_key","user_id","place_name","province","lat","lon","created_at","updated_at"},
        "reviews": {"id","marker_id","user_id","rating","comment","created_at","updated_at"},
        "settings": {"key","value"},
    }
    for t, cols in need.items():
        have = _table_cols(conn, t)
        if not cols.issubset(have):
            return False
    return True

def init_db(reset_if_mismatch: bool = True):
    """Create DB schema. Auto-backup + recreate if an older/incorrect DB exists."""
    db_file = Path(DB_PATH)
    schema_path = Path(__file__).with_name("schema.sql")
    db_file.parent.mkdir(parents=True, exist_ok=True)

    if db_file.exists() and reset_if_mismatch:
        ok = False
        try:
            conn = sqlite3.connect(str(db_file))
            ok = _schema_ok(conn)
            conn.close()
        except Exception:
            ok = False
        if not ok:
            ts = int(time.time())
            backup = db_file.with_name(f"{db_file.stem}_backup_{ts}{db_file.suffix}")
            try:
                db_file.replace(backup)
            except Exception:
                try:
                    db_file.unlink()
                except Exception:
                    pass

    conn = sqlite3.connect(str(db_file))
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    # --- lightweight migrations (keep existing data) ---
    # Add group_name column if missing (required for "ระบบกลุ่มผัก")
    try:
        cols = _table_cols(conn, "vegetables")
        if "group_name" not in cols:
            conn.execute("ALTER TABLE vegetables ADD COLUMN group_name TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

def get_setting(key: str, default: str | None = None) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()

def seed_if_empty():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM vegetables").fetchone()
        if row["n"] > 0:
            return

        # 6 classes (EfficientNetB2) – show Thai first, then other names in UI
        vegs = [
            ("makwaen","มะแขว่น","Makwaen","มะแขว่น/พริกขี้หนูป่า (บางพื้นที่)","Zanthoxylum limonella",
             "น้ำมันหอมระเหยตามธรรมชาติ • ใยอาหาร (ขึ้นกับส่วนที่กิน)",
             "คั่ว/ตำพริกแกง • ใส่น้ำพริก/แกงอ่อม • โรยเพิ่มกลิ่นหอมซ่า"),
            ("neem","สะเดา","Neem","สะเดาไทย/สะเดาแดง (พบเรียกต่างกัน)","Azadirachta indica",
             "ใยอาหาร • สารต้านอนุมูลอิสระจากพืช",
             "ลวกเพื่อลดความขม • กินคู่ปลาย่าง/น้ำปลาหวาน • ใส่แกง"),
            ("paracress","ผักคราด","Para Cress","ผักคราดหัวแหวน/Spilanthes (ชื่อเรียกเดิม)","Acmella oleracea",
             "ใยอาหาร • วิตามิน A/C (ขึ้นกับความสด)",
             "กินสดแนม • ใส่ยำ/ส้มตำ • ผัดไฟแรงให้กรอบ"),
            ("rattailed_radish","ผักขี้หูด","French radis","หัวไชเท้าฝรั่ง/เรดิช","Raphanus sativus",
             "วิตามิน C • ใยอาหาร • น้ำสูง",
             "กินสด/สลัด • ดองเปรี้ยว • ผัด/ลวกจิ้มน้ำพริก"),
            ("tupistra","นางแลว","Tupistra","นางแลว (เรียกท้องถิ่น)","Tupistra albiflora",
             "ใยอาหาร • แร่ธาตุจากพืชใบเขียว (ขึ้นกับพื้นที่)",
             "ลวก/นึ่งจิ้มน้ำพริก • แกงแค/แกงผักรวม • ผัดกระเทียม"),
            ("salae","สะแล","Salae","สะแล","Bauhinia sp.",
             "ใยอาหาร • วิตามินจากยอดอ่อน",
             "แกงแค • ลวกจิ้มน้ำพริก • ผัดน้ำมันหอย"),
        ]

        conn.executemany(
            "INSERT INTO vegetables (class_key,thai_name,en_name,other_names,scientific_name,nutrition,cooking) VALUES (?,?,?,?,?,?,?)",
            vegs,
        )
        conn.commit()
@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()
