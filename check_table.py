import sqlite3
conn = sqlite3.connect('interview_agent.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='course_chat_messages'").fetchall()
print(tables)
conn.close()
