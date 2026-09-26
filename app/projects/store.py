"""Per-project SQLite store with resumable stage state."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, source TEXT NOT NULL, srt TEXT, audio TEXT, settings TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates (id INTEGER PRIMARY KEY, data TEXT NOT NULL, score REAL NOT NULL, selected INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS shorts (candidate_id INTEGER PRIMARY KEY, video TEXT, captions TEXT, status TEXT NOT NULL DEFAULT 'SELECTED', metadata TEXT, thumbnail TEXT, youtube_id TEXT);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, candidate_id INTEGER, status TEXT NOT NULL, error TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

class ProjectStore:
    def __init__(self, folder: str | Path, recover: bool = True):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        for sub in ('shorts', 'captions', 'metadata', 'thumbnails', 'previews', 'logs'):
            (self.folder / sub).mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.folder / 'project.db')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if recover:
            self.db.execute("UPDATE jobs SET status='PENDING', error='Interrupted; ready to retry' WHERE status='RUNNING'")
            self.db.commit()

    def close(self):
        self.db.close()

    def create(self, name, source, srt, audio, settings):
        self.db.execute('INSERT INTO projects VALUES (1,?,?,?,?,?,?)',
                        (name, str(source), str(srt) if srt else None,
                         str(audio) if audio else None, json.dumps(settings),
                         datetime.now(timezone.utc).isoformat()))
        self.db.commit()
        (self.folder / 'project.json').write_text(json.dumps(dict(self.project()), indent=2), encoding='utf-8')

    def project(self):
        row = self.db.execute('SELECT * FROM projects WHERE id=1').fetchone()
        return dict(row) if row else None

    def candidates(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM candidates ORDER BY score DESC')]

    def save_candidates(self, items):
        # Preserve selected/edited candidates across reanalysis; AI ids are not stable.
        existing = {row['id']: row for row in self.candidates() if row['selected']}
        with self.db:
            self.db.execute('DELETE FROM candidates WHERE selected=0')
            for candidate, value in items:
                if candidate.id in existing:
                    continue
                self.db.execute('INSERT INTO candidates (id,data,score) VALUES (?,?,?)',
                                (candidate.id, candidate.model_dump_json(), value))
        (self.folder / 'analysis.json').write_text(
            json.dumps([json.loads(row['data']) for row in self.candidates()], indent=2), encoding='utf-8')

    def select(self, candidate_id, selected):
        with self.db:
            self.db.execute('UPDATE candidates SET selected=? WHERE id=?', (int(selected), candidate_id))
            if selected:
                self.db.execute('INSERT OR IGNORE INTO shorts (candidate_id,status) VALUES (?,?)', (candidate_id, 'SELECTED'))
            else:
                self.db.execute("DELETE FROM shorts WHERE candidate_id=? AND status='SELECTED'", (candidate_id,))

    def shorts(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM shorts ORDER BY candidate_id')]

    def update_short(self, candidate_id, **fields):
        allowed = {'video', 'captions', 'status', 'metadata', 'thumbnail', 'youtube_id'}
        if not fields or set(fields) - allowed:
            raise ValueError('Invalid short fields')
        with self.db:
            self.db.execute(f"UPDATE shorts SET {', '.join(k+'=?' for k in fields)} WHERE candidate_id=?",
                            (*fields.values(), candidate_id))

    def job(self, kind, candidate_id=None):
        with self.db:
            cursor = self.db.execute('INSERT INTO jobs (kind,candidate_id,status,created) VALUES (?,?,?,?)',
                                     (kind, candidate_id, 'PENDING', datetime.now(timezone.utc).isoformat()))
        return cursor.lastrowid

    def job_status(self, job_id, status, error=None):
        with self.db:
            self.db.execute('UPDATE jobs SET status=?, error=? WHERE id=?', (status, error, job_id))

    def jobs(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM jobs ORDER BY id DESC')]

    def cached(self, key):
        row = self.db.execute('SELECT value FROM cache WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)', (key, json.dumps(value)))
