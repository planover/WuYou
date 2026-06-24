from app.services.sync.sync_engine import build_uid_range


def test_build_uid_range_from_zero():
    assert build_uid_range(0) == "1:*"


def test_build_uid_range_from_nine():
    assert build_uid_range(9) == "10:*"

