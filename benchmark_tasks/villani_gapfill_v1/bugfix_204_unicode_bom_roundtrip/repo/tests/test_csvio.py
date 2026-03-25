from app.csvio import import_rows, export_rows

def test_import_handles_utf8_bom():
    rows = import_rows("﻿name,city
José,São Paulo
")
    assert rows == [{"name": "José", "city": "São Paulo"}]

def test_roundtrip_keeps_unicode():
    rows = [{"name": "Miyu", "city": "東京"}]
    text = export_rows(rows)
    assert import_rows(text) == rows
