def normalize_doc(doc, metadata_key):
    out=dict(doc); value=out.get(metadata_key)
    if value is not None: out["section"] = value
    return out
