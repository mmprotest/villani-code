import csv
from io import StringIO

def import_rows(text: str):
    reader = csv.DictReader(StringIO(text))
    return list(reader)

def export_rows(rows):
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=["name", "city"])
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()
