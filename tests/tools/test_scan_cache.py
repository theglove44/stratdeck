from stratdeck.tools.scan_cache import attach_ivr_to_scan_rows


def test_attach_ivr_to_scan_rows_accepts_nested_snapshot():
    rows = [{"symbol": "SPX"}]
    iv_snapshot = {"SPX": {"ivr": 0.32}}

    result = attach_ivr_to_scan_rows(rows, iv_snapshot)

    assert result[0]["ivr"] == 0.32
