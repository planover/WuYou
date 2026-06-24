from app.services.sync.folder_discovery import classify_folder


def test_classify_by_special_use():
    assert classify_folder(imap_name="[Gmail]/Sent Mail", flags=["\\\\Sent"]) == "sent"
    assert classify_folder(imap_name="[Gmail]/Trash", flags=["\\\\Trash"]) == "trash"
    assert classify_folder(imap_name="[Gmail]/Spam", flags=["\\\\Junk"]) == "junk"


def test_classify_by_guess():
    assert classify_folder(imap_name="已发送", flags=[]) == "sent"
    assert classify_folder(imap_name="垃圾邮件", flags=[]) == "junk"
    assert classify_folder(imap_name="Archive", flags=[]) == "archive"


def test_classify_fallback_custom():
    assert classify_folder(imap_name="项目组-通知", flags=[]) == "custom"

