"""SQLite accounts and conversations. Every conversation query is owner scoped."""
import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path, demo_user_ids=()):
        self.path = str(path)
        self.demo_user_ids = tuple(dict.fromkeys(demo_user_ids))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL, password_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS logins (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS report_assignments (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                demo_user_id TEXT UNIQUE NOT NULL, assigned_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                lease TEXT, lease_until REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
                sources TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS owner_updated ON conversations(user_id, updated);
            CREATE INDEX IF NOT EXISTS conversation_created ON messages(conversation_id, created);
            """)
            db.execute('BEGIN IMMEDIATE')
            self._assign_report_users(db)

    def _assign_report_users(self, db):
        """Caller holds a write transaction; preserve existing assignments."""
        used = {row[0] for row in db.execute('SELECT demo_user_id FROM report_assignments')}
        available = iter(value for value in self.demo_user_ids if value not in used)
        # Legacy users have no created timestamp; rowid reflects insertion order.
        users = db.execute('''SELECT users.id FROM users
            LEFT JOIN report_assignments ON users.id=report_assignments.user_id
            WHERE report_assignments.user_id IS NULL ORDER BY users.rowid''').fetchall()
        for user in users:
            demo_id = next(available, None)
            if demo_id is None:
                break
            db.execute('INSERT INTO report_assignments VALUES (?,?,?)', (user['id'], demo_id, time.time()))

    def report_user_id(self, user_id):
        with self.connect() as db:
            row = db.execute('SELECT demo_user_id FROM report_assignments WHERE user_id=?', (user_id,)).fetchone()
            return row[0] if row else None

    @staticmethod
    def password_hash(password, salt):
        return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

    def register(self, username, password):
        salt = secrets.token_hex(16)
        user = {"id": str(uuid.uuid4()), "username": username}
        hashed = self.password_hash(password, salt)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO users VALUES (?,?,?,?)", (user['id'], username, salt, hashed))
            self._assign_report_users(db)
        return user

    def authenticate(self, username, password):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        # Also compute a hash for missing users to avoid a fast username oracle.
        hashed = self.password_hash(password, row['salt'] if row else '00' * 16)
        if row and hmac.compare_digest(hashed, row['password_hash']):
            return {"id": row['id'], "username": row['username']}

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def login(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM logins WHERE expires<?", (time.time(),))
            db.execute("INSERT INTO logins VALUES (?,?,?)", (self.token_hash(token), user_id, time.time() + 604800))
        return token

    def user_for_token(self, token):
        with self.connect() as db:
            row = db.execute("""SELECT users.id, users.username FROM logins JOIN users ON users.id=logins.user_id
                WHERE token_hash=? AND expires>?""", (self.token_hash(token), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM logins WHERE token_hash=?", (self.token_hash(token),))

    def conversations(self, user_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,title,created,updated FROM conversations WHERE user_id=? ORDER BY updated DESC", (user_id,))]

    def create_conversation(self, user_id):
        item = {"id": str(uuid.uuid4()), "title": "新对话", "created": time.time(), "updated": time.time()}
        with self.connect() as db:
            db.execute("INSERT INTO conversations(id,user_id,title,created,updated) VALUES (?,?,?,?,?)", (item['id'], user_id, item['title'], item['created'], item['updated']))
        return item

    def get_conversation(self, user_id, conversation_id):
        import json
        with self.connect() as db:
            row = db.execute("SELECT id,title,created,updated FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)).fetchone()
            if not row:
                return None
            messages = [dict(r) for r in db.execute("SELECT id,role,content,status,sources,created FROM messages WHERE conversation_id=? ORDER BY created,rowid", (conversation_id,))]
            for message in messages:
                message['sources'] = json.loads(message['sources'])
            return {**dict(row), "messages": messages}

    def delete_conversation(self, user_id, conversation_id):
        with self.connect() as db:
            return db.execute("DELETE FROM conversations WHERE id=? AND user_id=? AND lease_until<?", (conversation_id, user_id, time.time())).rowcount > 0

    def begin_turn(self, user_id, conversation_id, content):
        lease, answer_id = str(uuid.uuid4()), str(uuid.uuid4())
        now = time.time()
        with self.connect() as db:
            changed = db.execute("UPDATE conversations SET lease=?,lease_until=?,updated=? WHERE id=? AND user_id=? AND lease_until<?", (lease, now + 240, now, conversation_id, user_id, now)).rowcount
            if not changed:
                return None
            # Expired workers cannot commit a late answer over a new turn.
            db.execute("UPDATE messages SET status='interrupted' WHERE conversation_id=? AND status='streaming'", (conversation_id,))
            db.execute("INSERT INTO messages(id,conversation_id,role,content,status,created) VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), conversation_id, 'user', content, 'complete', now))
            db.execute("INSERT INTO messages(id,conversation_id,role,content,status,created) VALUES (?,?,?,?,?,?)", (answer_id, conversation_id, 'assistant', '', 'streaming', now + 0.000001))
            db.execute("UPDATE conversations SET title=? WHERE id=? AND title='新对话'", (content[:30], conversation_id))
        return lease, answer_id

    def finish_turn(self, user_id, conversation_id, lease, answer_id, content, status, sources):
        import json
        with self.connect() as db:
            changed = db.execute("UPDATE conversations SET lease=NULL,lease_until=0,updated=? WHERE id=? AND user_id=? AND lease=?", (time.time(), conversation_id, user_id, lease)).rowcount
            if changed:
                db.execute("UPDATE messages SET content=?,status=?,sources=? WHERE id=? AND conversation_id=?", (content, status, json.dumps(sources, ensure_ascii=False), answer_id, conversation_id))
