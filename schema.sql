-- LannaVeg schema (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,          -- guest/google/facebook/line
  provider_sub TEXT NOT NULL,      -- subject/uid from provider
  display_name TEXT,
  email TEXT,
  avatar_url TEXT,
  password_hash TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_provider_sub ON users(provider, provider_sub);

-- Vegetables (6 classes)
CREATE TABLE IF NOT EXISTS vegetables (
  class_key TEXT PRIMARY KEY,      -- model class key
  thai_name TEXT NOT NULL,
  en_name TEXT,
  other_names TEXT,
  scientific_name TEXT,
  nutrition TEXT,
  cooking TEXT,
  group_name TEXT                 -- e.g., ผักพื้นบ้าน/สมุนไพร/เครื่องเทศ
);

-- Community map markers
CREATE TABLE IF NOT EXISTS markers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  veg_key TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  place_name TEXT,
  province TEXT,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (veg_key) REFERENCES vegetables(class_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_markers_veg_key ON markers(veg_key);
CREATE INDEX IF NOT EXISTS idx_markers_province ON markers(province);

-- Reviews (one marker can have many reviews; we also keep latest rating at marker-level in UI)
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  marker_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  rating INTEGER NOT NULL,
  comment TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (marker_id) REFERENCES markers(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reviews_marker_id ON reviews(marker_id);
CREATE INDEX IF NOT EXISTS idx_reviews_user_id ON reviews(user_id);

-- App settings
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
