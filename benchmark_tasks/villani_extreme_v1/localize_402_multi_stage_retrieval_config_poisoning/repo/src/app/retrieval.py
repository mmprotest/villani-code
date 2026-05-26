from .loader import load_profile
from .metadata import normalize_doc

def retrieve(docs, query, profile=None):
    cfg=load_profile(profile)
    normalized=[normalize_doc(doc, cfg["normalize_metadata_key"]) for doc in docs]
    filtered=[d for d in normalized if d.get("section") in cfg["allowed_sections"]]
    ranked=sorted(filtered, key=lambda d: (query.lower() in d["text"].lower(), len(d["text"])), reverse=True)
    return [d["id"] for d in ranked]
