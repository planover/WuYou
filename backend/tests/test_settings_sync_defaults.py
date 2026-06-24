import os

from app.core.config import Settings


def test_settings_sync_defaults():
    # Avoid accidental overrides from the environment.
    for key in (
        "WUYOU_SYNC_MODE",
        "WUYOU_SYNC_INTERVAL_MINUTES",
        "WUYOU_SYNC_CONCURRENCY",
        "WUYOU_SYNC_FOLDERS_DEFAULT",
    ):
        os.environ.pop(key, None)

    settings = Settings(_env_file=None)

    assert settings.sync_mode == "inprocess"
    assert settings.sync_interval_minutes == 30
    assert settings.sync_concurrency == 2
    assert settings.sync_folders_default == ["inbox", "sent", "trash", "archive", "junk"]
