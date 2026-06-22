"""Ek dafa chalane wala script - courses table mein isCompanyCourse column add karta hai
(agar pehle se nahi hai). Backend folder mein (jahan main.py hai) save karo, phir terminal mein:
    python add_company_course_column.py
Server band hona chahiye jab ye chalao."""

import sqlite3

# Agar tumhara DB file kisi aur naam/jagah ka hai to yahan path badal do
conn = sqlite3.connect("interview_agent.db")
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE courses ADD COLUMN isCompanyCourse BOOLEAN DEFAULT 0")
    conn.commit()
    print("Success: isCompanyCourse column added.")
except sqlite3.OperationalError as e:
    print(f"Skipped (probably already exists): {e}")

conn.close()
print("Done.")