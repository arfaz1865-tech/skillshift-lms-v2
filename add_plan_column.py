"""Ek dafa chalane wala script - companies table mein planTier column add karta hai.
Backend folder mein (jahan main.py hai) save karo, phir terminal mein:
    python add_plan_column.py
Server band hona chahiye jab ye chalao."""

import sqlite3

# Agar tumhara DB file kisi aur naam/jagah ka hai to yahan path badal do
conn = sqlite3.connect("interview_agent.db")
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE companies ADD COLUMN planTier TEXT DEFAULT 'free'")
    conn.commit()
    print("Success: planTier column added.")
except sqlite3.OperationalError as e:
    print(f"Skipped (probably already exists): {e}")

# Existing companies ko free plan + uski limit assign kar do
cursor.execute("UPDATE companies SET planTier = 'free' WHERE planTier IS NULL")
cursor.execute("UPDATE companies SET invitationLimit = 3 WHERE planTier = 'free'")
conn.commit()
conn.close()
print("Done.")