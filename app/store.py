"""Each companion owns a SQLite database; source snapshots are insert-only."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from core import job_identity, public_job_url


def now():
    return datetime.now(timezone.utc).isoformat()


def salary_bounds(raw):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*[Kk](?:\s*[·•]\s*\d+薪)?\s*', raw or '')
    if not match:
        return None, None
    low, high = [round(float(v) * 1000) for v in match.groups()]
    return (low, high) if 0 <= low <= high else (None, None)


def size_lower_bound(raw):
    text = (raw or '').replace(',', '').replace(' ', '')
    match = re.fullmatch(r'(\d+)(?:[-－](\d+)人|人以上)', text)
    if not match:
        return None
    low = int(match.group(1))
    if match.group(2) and low > int(match.group(2)):
        return None
    return low


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    source TEXT NOT NULL,
                    source_job_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    salary TEXT NOT NULL,
                    salary_raw TEXT,
                    salary_months INTEGER,
                    salary_min INTEGER,
                    salary_max INTEGER,
                    work_location TEXT NOT NULL,
                    city TEXT,
                    district TEXT,
                    company_size TEXT,
                    size_min INTEGER,
                    experience TEXT,
                    education TEXT,
                    employment_type TEXT,
                    jd TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    follow_state TEXT NOT NULL DEFAULT 'unmarked'
                        CHECK(follow_state IN ('unmarked','follow','unfollow')),
                    follow_changed_at TEXT,
                    PRIMARY KEY(source, source_job_id)
                );
                CREATE TABLE IF NOT EXISTS collection_runs (
                    id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL,
                    reason TEXT,
                    counts_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS collection_items (
                    run_id TEXT NOT NULL REFERENCES collection_runs(id),
                    ordinal INTEGER NOT NULL,
                    source_job_id TEXT,
                    batch INTEGER NOT NULL,
                    title TEXT,
                    company TEXT,
                    outcome TEXT NOT NULL,
                    reason TEXT,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, ordinal)
                );
            ''')

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def exists(self, source, job_id):
        with self.connection() as conn:
            return conn.execute('SELECT 1 FROM jobs WHERE source=? AND source_job_id=?',
                                (source, job_id)).fetchone() is not None

    def add_job(self, facts):
        source, job_id = job_identity(facts['url'])
        for key in ('title', 'company', 'salary', 'work_location', 'jd'):
            if not isinstance(facts.get(key), str) or not facts[key].strip():
                raise ValueError('岗位缺少必要字段：' + key)
        for key in ('salary', 'company_size'):
            if any('\ue000' <= ch <= '\uf8ff' for ch in facts.get(key, '') or ''):
                raise ValueError('字段字体尚未解释：' + key)
        low, high = salary_bounds(facts['salary'])
        stamp = now()
        data = {
            'source': source, 'source_job_id': job_id, 'url': public_job_url(facts['url']),
            **{k: facts[k].strip() for k in ('title', 'company', 'salary', 'work_location', 'jd')},
            'salary_min': low, 'salary_max': high,
            'salary_raw': facts.get('salary_raw') or facts['salary'],
            'salary_months': int(m.group(1)) if (m:=re.search(r'[·•]\s*(\d+)薪',facts['salary'])) else None,
            **{k: facts.get(k) or None for k in ('city', 'district', 'company_size',
                'experience', 'education', 'employment_type')},
            'size_min': size_lower_bound(facts.get('company_size')),
            'first_seen_at': stamp, 'last_seen_at': stamp,
        }
        names = list(data)
        with self.connection() as conn:
            # Existing snapshots/follow values are never overwritten by collection.
            cur = conn.execute(
                'INSERT OR IGNORE INTO jobs (' + ','.join(names) + ') VALUES ('
                + ','.join('?' for _ in names) + ')', tuple(data.values()))
            return cur.rowcount == 1

    def seen_again(self, source, job_id):
        with self.connection() as conn:
            conn.execute('UPDATE jobs SET last_seen_at=? WHERE source=? AND source_job_id=?',
                         (now(), source, job_id))

    def follow(self, source, job_id, state):
        if state not in ('unmarked', 'follow', 'unfollow'):
            raise ValueError('无效关注状态')
        with self.connection() as conn:
            cur = conn.execute('UPDATE jobs SET follow_state=?,follow_changed_at=? '
                               'WHERE source=? AND source_job_id=?', (state, now(), source, job_id))
            if not cur.rowcount:
                raise KeyError(job_id)

    def query(self, query='', districts=(), salary_low=None, salary_high=None,
              size_min=None, follow_state='all'):
        clauses, values = [], []
        if query:
            clauses.append('(title LIKE ? OR company LIKE ? OR jd LIKE ?)')
            values.extend(['%' + query + '%'] * 3)
        if districts:
            clauses.append('district IN (' + ','.join('?' for _ in districts) + ')')
            values.extend(districts)
        if salary_low is not None:
            clauses.append('salary_max >= ?'); values.append(salary_low)
        if salary_high is not None:
            clauses.append('salary_min <= ?'); values.append(salary_high)
        if size_min is not None:
            clauses.append('size_min >= ?'); values.append(size_min)
        if follow_state != 'all':
            if follow_state not in ('unmarked', 'follow', 'unfollow'):
                raise ValueError('无效关注筛选')
            clauses.append('follow_state=?'); values.append(follow_state)
        sql = 'SELECT * FROM jobs' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(sql + ' ORDER BY first_seen_at DESC,source_job_id', values)]

    def start_run(self, run_id, config):
        with self.connection() as conn:
            conn.execute('INSERT INTO collection_runs(id,config_json,started_at,status) VALUES(?,?,?,?)',
                         (run_id, json.dumps(config, ensure_ascii=False), now(), 'running'))

    def run_item(self, run_id, ordinal, batch, job_id, title, company, outcome, reason):
        with self.connection() as conn:
            conn.execute('INSERT INTO collection_items VALUES(?,?,?,?,?,?,?,?,?)',
                         (run_id, ordinal, job_id, batch, title, company, outcome, reason, now()))

    def finish_run(self, run_id, status, reason, counts):
        with self.connection() as conn:
            conn.execute('UPDATE collection_runs SET ended_at=?,status=?,reason=?,counts_json=? WHERE id=?',
                         (now(), status, reason, json.dumps(counts, ensure_ascii=False), run_id))

    def update_counts(self, run_id, counts):
        with self.connection() as conn:
            conn.execute('UPDATE collection_runs SET counts_json=? WHERE id=?',
                         (json.dumps(counts,ensure_ascii=False),run_id))

    def run_state(self, run_id, status, reason):
        # Pause/resume changes the same live run; never reset its counts or clock.
        if status not in {'running', 'paused'}: raise ValueError('无效活动状态')
        with self.connection() as conn:
            conn.execute('UPDATE collection_runs SET status=?,reason=? WHERE id=? AND ended_at IS NULL',
                         (status, reason, run_id))

    def recent_runs(self):
        with self.connection() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM collection_runs ORDER BY started_at DESC LIMIT 10')]

    def items(self, run_id):
        with self.connection() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM collection_items WHERE run_id=? ORDER BY ordinal', (run_id,))]

    def backup(self, destination):
        destination = Path(destination)
        if destination.exists(): raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            with self.connection() as conn: conn.backup(target)
        finally:
            target.close()
