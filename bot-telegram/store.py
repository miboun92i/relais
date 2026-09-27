"""Store SQLite Relais."""
import json, sqlite3, time
from datetime import datetime, timezone
from style import DEFAULTS, DailyLimitReached

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'auto', unread INTEGER NOT NULL DEFAULT 0,
            preview TEXT NOT NULL DEFAULT '', updated REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL, telegram_id INTEGER NOT NULL, source TEXT NOT NULL,
            text TEXT NOT NULL, created REAL NOT NULL, UNIQUE(chat_id,telegram_id));
        CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id,id);
        CREATE INDEX IF NOT EXISTS idx_chats_updated ON chats(updated DESC);
        CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL,
            text TEXT NOT NULL, status TEXT NOT NULL, message_id INTEGER);
        ''')
        self.db.execute('INSERT OR IGNORE INTO settings VALUES (1,?)', (json.dumps(DEFAULTS),))
        cols = {row[1] for row in self.db.execute('PRAGMA table_info(chats)')}
        if 'tease_sent' not in cols:
            self.db.execute('ALTER TABLE chats ADD COLUMN tease_sent INTEGER NOT NULL DEFAULT 0')
        self.db.commit()
    def settings(self):
        value = json.loads(self.db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        for key, default in DEFAULTS.items():
            value.setdefault(key, default)
        return value
    def save_settings(self, settings):
        self.db.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(settings),))
        self.db.commit()
    def ensure(self, chat_id, name):
        self.db.execute('INSERT INTO chats(id,name,updated) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name', (chat_id, name, time.time()))
        self.db.commit()
    def chat(self, chat_id):
        row = self.db.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        return dict(row) if row else None
    def chats(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM chats ORDER BY updated DESC')]
    def mode(self, chat_id, mode):
        self.db.execute('UPDATE chats SET mode=?,revision=revision+1 WHERE id=?', (mode, chat_id))
        self.db.commit()
    def read(self, chat_id):
        self.db.execute('UPDATE chats SET unread=0 WHERE id=?', (chat_id,))
        self.db.commit()
    def known(self, chat_id, message_id):
        return self.db.execute('SELECT 1 FROM messages WHERE chat_id=? AND telegram_id=?', (chat_id, message_id)).fetchone() is not None
    def add(self, chat_id, message_id, source, text, created=None):
        created = created or time.time()
        cursor = self.db.execute('INSERT OR IGNORE INTO messages(chat_id,telegram_id,source,text,created) VALUES(?,?,?,?,?)', (chat_id, message_id, source, text, created))
        inserted = bool(cursor.rowcount)
        if inserted:
            self.db.execute('UPDATE chats SET preview=?,updated=?,unread=unread+?,revision=revision+1 WHERE id=?', (text[:160], created, int(source == 'client'), chat_id))
        self.db.commit()
        return inserted
    def messages(self, chat_id, limit=200):
        rows = self.db.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?', (chat_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]
    def usage(self):
        day = datetime.now(timezone.utc).date().isoformat()
        row = self.db.execute('SELECT count FROM usage WHERE day=?', (day,)).fetchone()
        return row[0] if row else 0
    def reserve(self):
        day = datetime.now(timezone.utc).date().isoformat()
        if self.usage() >= self.settings()['daily_limit']:
            raise DailyLimitReached('Plafond quotidien de demandes IA atteint.')
        self.db.execute('INSERT INTO usage VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1', (day,))
        self.db.commit()
    def claim_request(self, request_id, chat_id, text):
        row = self.db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        if row:
            if row['chat_id'] != chat_id or row['text'] != text:
                raise ValueError('Identifiant deja utilise pour un autre message.')
            return dict(row)
        self.db.execute("INSERT INTO requests VALUES(?,?,?,'pending',NULL)", (request_id, chat_id, text))
        self.db.commit()
        return None
    def finish_request(self, request_id, message_id):
        self.db.execute("UPDATE requests SET status='sent',message_id=? WHERE id=?", (message_id, request_id))
        self.db.commit()
    def mark_tease_sent(self, chat_id):
        self.db.execute('UPDATE chats SET tease_sent=tease_sent+1 WHERE id=?', (chat_id,))
        self.db.commit()
    def clear_tease_sent(self, chat_id):
        self.db.execute('UPDATE chats SET tease_sent=0 WHERE id=?', (chat_id,))
        self.db.commit()
