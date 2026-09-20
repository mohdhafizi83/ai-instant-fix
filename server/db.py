import sqlite3
import os

DB_PATH = os.environ.get('AIF_DB', os.path.join(os.path.dirname(__file__), 'tasks.db'))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ai_instant_task (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            prompt TEXT NOT NULL,
            url TEXT NOT NULL,
            path_files TEXT DEFAULT NULL,
            status TEXT NOT NULL DEFAULT 'task accepted',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()
    conn.close()


def create_task(user_id, prompt, url, path_files=None):
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO ai_instant_task (user_id, prompt, url, path_files, status) VALUES (?, ?, ?, ?, ?)',
        (user_id, prompt, url, path_files, 'task accepted')
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def get_tasks(user_id=None, status=None, limit=50):
    conn = get_db()
    query = 'SELECT * FROM ai_instant_task WHERE 1=1'
    params = []
    if user_id:
        query += ' AND user_id = ?'
        params.append(user_id)
    if status:
        query += ' AND status = ?'
        params.append(status)
    query += ' ORDER BY created_at DESC LIMIT ?'
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_task(task_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM ai_instant_task WHERE id = ?', (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_task_status(task_id, status):
    valid = ('task accepted', 'task on going', 'task completed', 'task rejected',
             'dangerous_stop', 'complex_send', 'prompt_ask')
    if status not in valid:
        raise ValueError(f'Invalid status: {status}')
    conn = get_db()
    conn.execute(
        "UPDATE ai_instant_task SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, task_id)
    )
    conn.commit()
    conn.close()


def count_tasks(user_id=None):
    conn = get_db()
    query = 'SELECT status, COUNT(*) as cnt FROM ai_instant_task'
    params = []
    if user_id:
        query += ' WHERE user_id = ?'
        params.append(user_id)
    query += ' GROUP BY status'
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {r['status']: r['cnt'] for r in rows}
