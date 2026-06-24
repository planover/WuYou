import sqlite3, os, hashlib, secrets
db = sqlite3.connect('/data/wuyou.sqlite3')
db.row_factory = sqlite3.Row

uid = secrets.token_hex(16)
salt = os.urandom(16).hex()
pwhash = hashlib.sha256(f"admin123{512}".encode()).hexdigest()  # simplified for test

# Check if admin exists
existing = db.execute("SELECT id FROM users WHERE username=?", ("admin",)).fetchone()
if existing:
    db.execute("UPDATE users SET email=?, hashed_password=?, salt=? WHERE id=?", 
               ("admin@wuyou.local", pwhash, salt, existing['id']))
    print(f"Updated user # {existing['id']}")
else:
    db.execute("INSERT INTO users (id, username, email, hashed_password, salt, created_at, status) VALUES (?,?,?,?,?,datetime('now'),?)",
               (uid, "admin", "admin@wuyou.local", pwhash, salt, "active"))
    print(f"Created admin ({uid})")

# Also create a simple test user
test_salt = os.urandom(16).hex()
test_pw = hashlib.sha256(f"test123{512}".encode()).hexdigest()
test_exist = db.execute("SELECT id FROM users WHERE username=?", ("demo",)).fetchone()
if test_exist:
    db.execute("UPDATE users SET email=?, hashed_password=?, salt=? WHERE id=?",
               ("demo@wuyou.local", test_pw, test_salt, test_exist['id']))
else:
    db.execute("INSERT INTO users (id, username, email, hashed_password, salt, created_at, status) VALUES (?,?,?,?,?,datetime('now'),?)",
               (secrets.token_hex(16), "demo", "demo@wuyou.local", test_pw, test_salt, "active"))

db.commit()
print("Done. Accounts: admin / demo")
db.close()
