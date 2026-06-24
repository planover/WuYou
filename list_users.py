import sqlite3
db = sqlite3.connect('/data/wuyou.sqlite3')
db.row_factory = sqlite3.Row
users = db.execute("SELECT id, username, email, created_at FROM users ORDER BY id DESC LIMIT 5").fetchall()
total = db.execute("SELECT count(id) AS c FROM users").fetchone()['c']
print(f"Total users: {total}")
for u in users:
    print(f"  #{u['id']} {u['username']} | {u['email']} | {u['created_at']}")
