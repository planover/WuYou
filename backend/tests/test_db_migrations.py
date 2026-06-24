import sqlite3

from app.core.database import Database


def test_db_migration_adds_messages_columns(tmp_path):
    db_path = tmp_path / "wuyou.sqlite3"

    # Create an "old" database where `messages` lacks `folder_role` / `imap_folder`.
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT
        );

        CREATE TABLE mailbox_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT
        );

        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            mailbox_id INTEGER REFERENCES mailbox_accounts(id) ON DELETE SET NULL,
            external_id TEXT NOT NULL,
            folder TEXT NOT NULL DEFAULT 'INBOX',
            subject TEXT NOT NULL,
            sender TEXT NOT NULL,
            recipients TEXT NOT NULL DEFAULT '[]',
            snippet TEXT NOT NULL DEFAULT '',
            body_text TEXT NOT NULL DEFAULT '',
            body_html TEXT NOT NULL DEFAULT '',
            raw_headers TEXT NOT NULL DEFAULT '{}',
            attachments_json TEXT NOT NULL DEFAULT '[]',
            unread INTEGER NOT NULL DEFAULT 1,
            starred INTEGER NOT NULL DEFAULT 0,
            has_attachments INTEGER NOT NULL DEFAULT 0,
            remote_content_allowed INTEGER NOT NULL DEFAULT 0,
            received_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, mailbox_id, external_id)
        );
        """
    )
    connection.execute("INSERT INTO users (id) VALUES (1)")
    connection.execute("INSERT INTO mailbox_accounts (id) VALUES (1)")
    connection.execute(
        """
        INSERT INTO messages (
            user_id,
            mailbox_id,
            external_id,
            folder,
            subject,
            sender,
            recipients,
            received_at,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            1,
            "ext-1",
            "INBOX",
            "subject",
            "sender@example.com",
            "[]",
            "2020-01-01T00:00:00Z",
            "2020-01-01T00:00:00Z",
            "2020-01-01T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    # Running init() should apply migrations to the existing DB.
    db = Database(db_path)
    db.init()

    columns = {row["name"] for row in db.connect().execute("PRAGMA table_info(messages)")}
    assert "folder_role" in columns
    assert "imap_folder" in columns

    row = db.query_one(
        "SELECT folder_role, imap_folder FROM messages WHERE external_id = ?",
        ("ext-1",),
    )
    assert row is not None
    assert row["folder_role"] == "inbox"
    assert row["imap_folder"] == "INBOX"

