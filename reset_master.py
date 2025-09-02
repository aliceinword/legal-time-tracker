import sqlite3, pathlib
from werkzeug.security import generate_password_hash

db = pathlib.Path.home() / ".legal_time_tracker_web" / "time_tracker.db"
print("DB path:", db)

con = sqlite3.connect(str(db))
cur = con.cursor()

cur.execute(
    "update users set password_hash=? where lower(email)=? or lower(name)=?",
    (generate_password_hash("ilovemyjob"), "law@local", "law"),
)
con.commit()

row = cur.execute(
    "select id, name, email from users where lower(email)=? or lower(name)=?",
    ("law@local", "law"),
).fetchone()
print("Reset for:", row)

con.close()
