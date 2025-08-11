# database.py
import sqlite3

class DatabaseManager:
    def __init__(self, db_file="manager.db"):
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, nametag TEXT)")
        self.conn.commit()

    def get_nametag(self, session_id):
        cursor = self.conn.execute("SELECT nametag FROM sessions WHERE session_id = ?", (session_id,))
        result = cursor.fetchone()
        return result[0] if result else ""

    def set_nametag(self, session_id, nametag):
        self.conn.execute("INSERT OR REPLACE INTO sessions (session_id, nametag) VALUES (?, ?)", (session_id, nametag))
        self.conn.commit()