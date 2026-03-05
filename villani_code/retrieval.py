from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from villani_code.indexing import RepoIndex


TOKEN_RE = re.compile(r"[^a-z0-9_]+")


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    return [tok for tok in TOKEN_RE.split(lowered) if tok]


@dataclass(frozen=True)
class Hit:
    path: str
    score: float
    reason: str


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self.doc_lengths = [len(d) for d in docs]
        self.avgdl = sum(self.doc_lengths) / len(self.doc_lengths) if docs else 0.0
        self.term_df: dict[str, int] = defaultdict(int)
        self.term_tf: list[Counter[str]] = []
        for doc in docs:
            tf = Counter(doc)
            self.term_tf.append(tf)
            for token in tf:
                self.term_df[token] += 1

    def score(self, query: list[str]) -> list[float]:
        n_docs = len(self.docs)
        scores = [0.0] * n_docs
        if not query or not n_docs:
            return scores
        for q in query:
            df = self.term_df.get(q, 0)
            if df == 0:
                continue
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            for idx, tf in enumerate(self.term_tf):
                freq = tf.get(q, 0)
                if not freq:
                    continue
                dl = self.doc_lengths[idx] or 1
                denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                scores[idx] += idf * (freq * (self.k1 + 1) / denom)
        return scores


class Retriever:
    def __init__(self, index: RepoIndex):
        self.index = index
        self.files = list(index.iter_files())
        self.docs: list[list[str]] = []
        for fi in self.files:
            doc_text = " ".join([fi.path, " ".join(fi.symbols), fi.snippet])
            self.docs.append(tokenize(doc_text))
        self.bm25 = BM25(self.docs)

    def query(self, text: str, k: int = 8) -> list[Hit]:
        q_tokens = tokenize(text)
        scores = self.bm25.score(q_tokens)
        ranked = sorted(enumerate(scores), key=lambda item: (-item[1], self.files[item[0]].path))
        hits: list[Hit] = []
        for idx, score in ranked:
            if score <= 0:
                continue
            fi = self.files[idx]
            reason = _build_reason(fi.path, fi.symbols, fi.snippet, q_tokens)
            hits.append(Hit(path=fi.path, score=score, reason=reason))
            if len(hits) >= k:
                break
        return hits


def _build_reason(path: str, symbols: list[str], snippet: str, query_tokens: list[str]) -> str:
    path_tokens = set(tokenize(path))
    symbol_tokens = {token for sym in symbols for token in tokenize(sym)}
    snippet_tokens = set(tokenize(snippet))

    for name, bag in (("path match", path_tokens), ("symbol match", symbol_tokens), ("snippet match", snippet_tokens)):
        matched = [tok for tok in query_tokens if tok in bag]
        if matched:
            return f"{name}: {', '.join(sorted(dict.fromkeys(matched))[:2])}"
    return "snippet match"
