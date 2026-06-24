import sqlite3
db = sqlite3.connect('/data/wuyou.sqlite3')
db.row_factory = sqlite3.Row

# Check content_items for tasks
tasks = db.execute("SELECT id, kind, title, meta_json FROM content_items WHERE kind='task' ORDER BY id DESC LIMIT 5").fetchall()
print(f"Task items: {len(tasks)}")
for t in tasks:
    print(f"  #{t['id']} kind={t['kind']} title={t['title']} meta={t['meta_json']}")

# Check the last item registered (likely from the test)
last = db.execute("SELECT id, kind, title, user_id FROM content_items ORDER BY id DESC LIMIT 3").fetchall()
print(f"\nLast 3 items:")
for l in last:
    print(f"  #{l['id']} kind={l['kind']} title={l['title']} user_id={l['user_id']}")

# Check API response format
contact_count = db.execute("SELECT count(*) as cnt FROM content_items WHERE kind='contact'").fetchone()['cnt']
note_count = db.execute("SELECT count(*) as cnt FROM content_items WHERE kind='note'").fetchone()['cnt']
task_count = db.execute("SELECT count(*) as cnt FROM content_items WHERE kind='task'").fetchone()['cnt']
print(f"\nCounts: contact={contact_count}, note={note_count}, task={task_count}")
