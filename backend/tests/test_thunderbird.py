"""Tests for Thunderbird prefs.js parsing, account extraction, and mbox parsing."""

import email
from email.policy import default as default_policy
from pathlib import Path

from app.services.thunderbird import (
    _list_mbox_files,
    extract_accounts_from_prefs,
    import_thunderbird_profile,
    parse_mbox,
    parse_prefs_js,
)

# ── Sample prefs.js content mimicking a Thunderbird profile with 2 IMAP accounts ──

SAMPLE_PREFS = """\
user_pref("mail.server.server1.hostname", "imap.gmail.com");
user_pref("mail.server.server1.name", "Gmail");
user_pref("mail.server.server1.port", 993);
user_pref("mail.server.server1.socketType", 3);
user_pref("mail.server.server1.type", "imap");
user_pref("mail.server.server1.userName", "alice@gmail.com");

user_pref("mail.server.server2.hostname", "imap.outlook.com");
user_pref("mail.server.server2.name", "Outlook");
user_pref("mail.server.server2.port", 993);
user_pref("mail.server.server2.socketType", 3);
user_pref("mail.server.server2.type", "imap");
user_pref("mail.server.server2.userName", "bob@outlook.com");

user_pref("mail.server.server3.hostname", "pop.gmail.com");
user_pref("mail.server.server3.type", "pop3");
user_pref("mail.server.server3.userName", "popuser@gmail.com");

user_pref("mail.identity.id1.fullName", "Alice");
user_pref("mail.identity.id1.useremail", "alice@gmail.com");
user_pref("mail.identity.id1.smtpServer", "smtp1");

user_pref("mail.identity.id2.fullName", "Bob");
user_pref("mail.identity.id2.useremail", "bob@outlook.com");
user_pref("mail.identity.id2.smtpServer", "smtp2");

user_pref("mail.smtpserver.smtp1.hostname", "smtp.gmail.com");
user_pref("mail.smtpserver.smtp1.port", 465);
user_pref("mail.smtpserver.smtp1.try_ssl", 3);

user_pref("mail.smtpserver.smtp2.hostname", "smtp.office365.com");
user_pref("mail.smtpserver.smtp2.port", 587);
user_pref("mail.smtpserver.smtp2.try_ssl", 2);

user_pref("mail.account.account1.identities", "id1");
user_pref("mail.account.account1.server", "server1");

user_pref("mail.account.account2.identities", "id2");
user_pref("mail.account.account2.server", "server2");

user_pref("mail.account.account3.server", "server3");
"""


class TestParsePrefsJs:
    def test_parse_prefs_js(self):
        prefs = parse_prefs_js(SAMPLE_PREFS)

        # 应正确提取 hostname / port / username
        assert prefs["mail.server.server1.hostname"] == "imap.gmail.com"
        assert prefs["mail.server.server1.port"] == "993"
        assert prefs["mail.server.server1.userName"] == "alice@gmail.com"
        assert prefs["mail.server.server2.hostname"] == "imap.outlook.com"
        assert prefs["mail.server.server2.port"] == "993"
        assert prefs["mail.server.server2.userName"] == "bob@outlook.com"

        # 布尔值也会转为字符串
        assert prefs.get("some.boolean") is None  # 本例中没有布尔值

    def test_extract_accounts(self):
        prefs = parse_prefs_js(SAMPLE_PREFS)
        accounts = extract_accounts_from_prefs(prefs)

        # 应提取 2 个 IMAP 账号 (server3 是 pop3, 会被过滤)
        assert len(accounts) == 2

        # 第一个账号
        acc1 = accounts[0]
        assert acc1["display_name"] == "Gmail"
        assert acc1["email"] == "alice@gmail.com"
        assert acc1["imap_host"] == "imap.gmail.com"
        assert acc1["imap_port"] == 993
        assert acc1["imap_ssl"] is True
        assert acc1["smtp_host"] == "smtp.gmail.com"
        assert acc1["smtp_port"] == 465
        assert acc1["smtp_ssl"] is True
        assert acc1["username"] == "alice@gmail.com"

        # 第二个账号 (smtp_ssl = False because try_ssl=2)
        acc2 = accounts[1]
        assert acc2["display_name"] == "Outlook"
        assert acc2["email"] == "bob@outlook.com"
        assert acc2["imap_host"] == "imap.outlook.com"
        assert acc2["imap_port"] == 993
        assert acc2["imap_ssl"] is True
        assert acc2["smtp_host"] == "smtp.office365.com"
        assert acc2["smtp_port"] == 587
        assert acc2["smtp_ssl"] is False  # try_ssl=2 -> not ssl
        assert acc2["username"] == "bob@outlook.com"

    def test_extract_accounts_filters_non_imap(self):
        """验证 server3 (pop3) 被正确过滤。"""
        prefs = parse_prefs_js(SAMPLE_PREFS)
        accounts = extract_accounts_from_prefs(prefs)

        # 不应出现 pop3 账号
        usernames = {a["username"] for a in accounts}
        assert "popuser@gmail.com" not in usernames


# ── Sample mbox content with 2 RFC822 emails ──

MBOX_CONTENT = b"""From alice@gmail.com Mon Jun 21 10:00:00 2026
From: Alice <alice@gmail.com>
To: Bob <bob@outlook.com>
Subject: Hello from Alice
Date: Mon, 21 Jun 2026 10:00:00 +0000
Message-ID: <msg1@example.com>
Content-Type: text/plain; charset="utf-8"

This is the first test email.
Best regards,
Alice

From bob@outlook.com Mon Jun 21 11:00:00 2026
From: Bob <bob@outlook.com>
To: Alice <alice@gmail.com>
Subject: Re: Hello from Alice
Date: Mon, 21 Jun 2026 11:00:00 +0000
Message-ID: <msg2@example.com>
Content-Type: text/plain; charset="utf-8"

Hi Alice,

Thanks for your email!
Bob
"""

# 第二组 mbox 内容（不同 Message-ID，避免 UNIQUE 约束去重）
MBOX_CONTENT_2 = b"""From alice@gmail.com Mon Jun 21 12:00:00 2026
From: Alice <alice@gmail.com>
To: Bob <bob@outlook.com>
Subject: Meeting tomorrow
Date: Mon, 21 Jun 2026 12:00:00 +0000
Message-ID: <msg3@example.com>
Content-Type: text/plain; charset="utf-8"

Let's meet at 10am.
Alice

From bob@outlook.com Mon Jun 21 13:00:00 2026
From: Bob <bob@outlook.com>
To: Alice <alice@gmail.com>
Subject: Re: Meeting tomorrow
Date: Mon, 21 Jun 2026 13:00:00 +0000
Message-ID: <msg4@example.com>
Content-Type: text/plain; charset="utf-8"

Sounds good!
Bob
"""


class TestParseMbox:
    def test_parse_mbox(self, tmp_path):
        mbox_path = tmp_path / "test.mbox"
        mbox_path.write_bytes(MBOX_CONTENT)

        messages = parse_mbox(mbox_path)

        # 应拆分为 2 封邮件
        assert len(messages) == 2

        # 验证每封邮件可被 email 模块正确解析
        parsed1 = email.message_from_bytes(messages[0], policy=default_policy)
        assert parsed1["Subject"] == "Hello from Alice"
        assert parsed1["Message-ID"] == "<msg1@example.com>"
        assert "This is the first test email." in str(parsed1)

        parsed2 = email.message_from_bytes(messages[1], policy=default_policy)
        assert parsed2["Subject"] == "Re: Hello from Alice"
        assert parsed2["Message-ID"] == "<msg2@example.com>"
        assert "Thanks for your email!" in str(parsed2)

    def test_parse_mbox_single_message(self, tmp_path):
        """单封邮件也应正确处理。"""
        single = b"""From alice@gmail.com Mon Jun 21 10:00:00 2026
From: Alice <alice@gmail.com>
To: Bob <bob@outlook.com>
Subject: Solo
Message-ID: <solo@example.com>

Just one.
"""
        mbox_path = tmp_path / "single.mbox"
        mbox_path.write_bytes(single)
        messages = parse_mbox(mbox_path)
        assert len(messages) == 1
        parsed = email.message_from_bytes(messages[0], policy=default_policy)
        assert parsed["Subject"] == "Solo"


class TestImportThunderbirdProfile:
    def test_import_full_profile(self, tmp_path, monkeypatch):
        """端到端测试：创建 fake profile 目录并导入。"""
        from app.core.database import Database

        # 使用临时数据库
        db_path = tmp_path / "wuyou.sqlite3"
        db = Database(db_path)
        db.init()

        # 创建一个测试用户
        db.execute(
            "INSERT INTO users (id, username, email, password_hash, created_at, updated_at) "
            "VALUES (1, 'test', 'test@test.com', 'hash', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )

        # 创建 profile 目录结构
        profile_dir = tmp_path / "thunderbird_profile"
        profile_dir.mkdir()
        (profile_dir / "prefs.js").write_text(SAMPLE_PREFS, encoding="utf-8")

        # 创建 ImapMail 目录结构与 mbox 文件
        imapmail = profile_dir / "ImapMail" / "imap.gmail.com"
        imapmail.mkdir(parents=True)
        (imapmail / "INBOX").write_bytes(MBOX_CONTENT)
        # .msf 文件应被忽略
        (imapmail / "INBOX.msf").write_text("index data")

        # 也创建一个子文件夹 (.sbd)
        sub = imapmail / "[Gmail].sbd"
        sub.mkdir()
        (sub / "Sent Mail").write_bytes(MBOX_CONTENT_2)
        (sub / "Sent Mail.msf").write_text("index")

        report = import_thunderbird_profile(profile_dir, db, user_id=1)

        assert report["accounts_parsed"] == 2
        assert report["accounts_created"] == 2
        assert report["folders_imported"] == 2  # INBOX + [Gmail]/Sent Mail
        assert report["messages_imported"] == 4  # 2 邮件 * 2 文件夹

        # 验证 mailbox_accounts 表
        accounts = db.query_all("SELECT * FROM mailbox_accounts WHERE user_id = 1")
        assert len(accounts) == 2
        assert accounts[0]["email_address"] == "alice@gmail.com"
        assert accounts[1]["email_address"] == "bob@outlook.com"
        for acc in accounts:
            assert acc["encrypted_secret"] == ""
            assert acc["sync_enabled"] == 0
            assert acc["provider"] == "thunderbird"

        # 验证 messages 表
        messages = db.query_all("SELECT * FROM messages WHERE user_id = 1")
        assert len(messages) == 4
        subjects = {m["subject"] for m in messages}
        assert "Hello from Alice" in subjects
        assert "Re: Hello from Alice" in subjects
        assert "Meeting tomorrow" in subjects
        assert "Re: Meeting tomorrow" in subjects

    def test_import_missing_prefs(self, tmp_path):
        """prefs.js 不存在时应抛出 FileNotFoundError。"""
        from app.core.database import Database

        db_path = tmp_path / "wuyou.sqlite3"
        db = Database(db_path)
        db.init()

        import pytest
        with pytest.raises(FileNotFoundError, match="prefs.js"):
            import_thunderbird_profile(tmp_path / "nonexistent", db, user_id=1)


class TestListMboxFiles:
    def test_list_mbox_files(self, tmp_path):
        """验证 _list_mbox_files 正确排除 .msf 并处理 .sbd 目录。"""
        server = tmp_path / "imap.example.com"
        server.mkdir(parents=True)
        (server / "INBOX").write_text("mbox content")
        (server / "INBOX.msf").write_text("index")
        (server / "Drafts").write_text("mbox content")
        (server / "Drafts.msf").write_text("index")

        sub = server / "Folder.sbd"
        sub.mkdir()
        (sub / "SubA").write_text("mbox")
        (sub / "SubA.msf").write_text("index")

        files = _list_mbox_files(server)

        paths = {(p.name, fn) for p, fn in files}
        assert ("INBOX", "INBOX") in paths
        assert ("Drafts", "Drafts") in paths
        assert ("SubA", "Folder/SubA") in paths

        # .msf 文件不应出现
        msf_names = [p.name for p, _ in files if p.name.endswith(".msf")]
        assert len(msf_names) == 0
        assert len(files) == 3
