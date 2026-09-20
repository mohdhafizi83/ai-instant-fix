CREATE TABLE IF NOT EXISTS ai_instant_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    url TEXT NOT NULL,
    path_files TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'task accepted',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Status lifecycle:
-- 'task accepted'  → baru diterima
-- 'task on going'  → Hermes Agent sedang kerjakan
-- 'task completed' → selesai
-- 'task rejected'  → ditolak
