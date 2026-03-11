from __future__ import annotations

import json

from villani_code.hypothesize.diversity import dedup_hypotheses, diversity_bucket
from villani_code.runtime.schemas import HypothesisClass, HypothesisRecord, SuspectRegion

DEFAULT_CLASSES = [
    HypothesisClass.CONTRACT_MISMATCH,
    HypothesisClass.NULL_OR_EMPTY_CASE,
    HypothesisClass.BOUNDARY_ERROR,
    HypothesisClass.PATH_OR_IMPORT_ERROR,
]


def _fallback_hypotheses(suspect: SuspectRegion, objective: str, max_items: int) -> list[HypothesisRecord]:
    generated: list[HypothesisRecord] = []
    for idx, cls in enumerate(DEFAULT_CLASSES[: max(2, min(7, max_items))], start=1):
        text = f"{suspect.file}: {cls.value.replace('_', ' ')} may violate objective '{objective[:80]}'"
        generated.append(
            HypothesisRecord(
                id=f"hyp-{suspect.file}-{idx}".replace("/", "_"),
                suspect_ref=suspect.file,
                text=text,
                hypothesis_class=cls,
                plausibility_score=max(0.1, suspect.score - (idx * 0.05)),
                diversity_bucket=diversity_bucket(cls),
                notes="fallback_template",
            )
        )
    return generated


def generate_hypotheses(suspect: SuspectRegion, objective: str, max_items: int = 5, runner: object | None = None) -> tuple[list[HypothesisRecord], list[HypothesisRecord], bool]:
    generated: list[HypothesisRecord] = []
    used_fallback = False
    if runner is not None:
        try:
            prompt = (
                "Return strict JSON list under key hypotheses. Each item has class,text,plausibility in [0,1]. "
                "Use 3-7 concise repair hypotheses grounded in evidence. Allowed classes: "
                + ",".join(c.value for c in HypothesisClass)
                + f". Objective: {objective}. Suspect: {suspect.file}."
            )
            raw = runner.client.create_message({"model": runner.model, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}], "max_tokens": 800, "stream": False}, stream=False)
            text = ""
            for block in (raw or {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text += str(block.get("text", ""))
            payload = json.loads(text or "{}")
            for idx, item in enumerate(payload.get("hypotheses", [])[:7], start=1):
                cls = HypothesisClass(item.get("class"))
                body = str(item.get("text", "")).strip()
                if not body:
                    continue
                generated.append(
                    HypothesisRecord(
                        id=f"hyp-{suspect.file}-{idx}".replace("/", "_"),
                        suspect_ref=suspect.file,
                        text=body,
                        hypothesis_class=cls,
                        plausibility_score=float(item.get("plausibility", max(0.1, suspect.score - idx * 0.03))),
                        diversity_bucket=diversity_bucket(cls),
                        notes="model_generated",
                    )
                )
        except Exception:  # noqa: BLE001
            used_fallback = True

    if not generated:
        used_fallback = True
        generated = _fallback_hypotheses(suspect, objective, max_items)

    kept, rejected = dedup_hypotheses(generated)
    if len({h.hypothesis_class for h in kept}) < 2 and len(kept) >= 2:
        rejected.append(kept.pop())
    return kept, rejected, used_fallback
