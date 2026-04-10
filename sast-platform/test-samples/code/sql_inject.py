"""
sql_inject.py — SQL injection via string concatenation.
Expected: Bandit B608 (possible SQL injection).
"""
import sqlite3

def get_user(username):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # B608: string concatenation directly into execute()
    query = "SELECT * FROM users WHERE name = '" + username + "'"
    cursor.execute(query)
    return cursor.fetchall()

def delete_user(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # B608: f-string in execute()
    cursor.execute(f"DELETE FROM users WHERE id = {user_id}")
    conn.commit()
