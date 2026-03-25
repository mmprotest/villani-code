from html import escape

def apply_filters(text):
    return text.strip()

def safe_escape(text):
    return escape(text)
