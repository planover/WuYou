ROLE_INBOX = "inbox"
ROLE_SENT = "sent"
ROLE_TRASH = "trash"
ROLE_ARCHIVE = "archive"
ROLE_JUNK = "junk"
ROLE_CUSTOM = "custom"

DEFAULT_ROLES = [ROLE_INBOX, ROLE_SENT, ROLE_TRASH, ROLE_ARCHIVE, ROLE_JUNK]

# RFC 6154 SPECIAL-USE flags. We map these server-declared roles first.
SPECIAL_USE_TO_ROLE = {
    r"\Sent": ROLE_SENT,
    r"\Trash": ROLE_TRASH,
    r"\Archive": ROLE_ARCHIVE,
    r"\Junk": ROLE_JUNK,
    r"\Spam": ROLE_JUNK,
}

# Fallback guessing by localized folder name (case-insensitive substring match).
GUESS_PATTERNS = {
    ROLE_SENT: ["sent", "已发送", "发件箱", "outbox"],
    ROLE_TRASH: ["trash", "deleted", "已删除", "垃圾箱"],
    ROLE_ARCHIVE: ["archive", "归档"],
    ROLE_JUNK: ["junk", "spam", "垃圾邮件"],
}

