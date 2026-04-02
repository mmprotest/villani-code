from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class PlanOption:
    id: str
    label: str
    description: str
    is_other: bool = False


@dataclass(slots=True)
class PlanQuestion:
    id: str
    question: str
    rationale: str
    options: list[PlanOption]

    def __post_init__(self) -> None:
        if len(self.options) != 4:
            raise ValueError("PlanQuestion must contain exactly 4 options")
        other = [opt for opt in self.options if opt.is_other]
        if len(other) != 1:
            raise ValueError("PlanQuestion must contain exactly one Other option")
        if other[0].label != "Other":
            raise ValueError('Other option label must be exactly "Other"')


@dataclass(slots=True)
class PlanAnswer:
    question_id: str
    selected_option_id: str
    other_text: str = ""


@dataclass(slots=True)
class PlanSessionResult:
    instruction: str
    task_summary: str
    candidate_files: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    recommended_steps: list[str] = field(default_factory=list)
    open_questions: list[PlanQuestion] = field(default_factory=list)
    resolved_answers: list[PlanAnswer] = field(default_factory=list)
    ready_to_execute: bool = False
    execution_brief: str = ""
    risk_level: str = "medium"
    confidence_score: float = 0.5

    def to_dict(self) -> dict[str, object]:
        return {
            "instruction": self.instruction,
            "task_summary": self.task_summary,
            "candidate_files": self.candidate_files,
            "assumptions": self.assumptions,
            "recommended_steps": self.recommended_steps,
            "open_questions": [
                {
                    "id": q.id,
                    "question": q.question,
                    "rationale": q.rationale,
                    "options": [
                        {
                            "id": o.id,
                            "label": o.label,
                            "description": o.description,
                            "is_other": o.is_other,
                        }
                        for o in q.options
                    ],
                }
                for q in self.open_questions
            ],
            "resolved_answers": [
                {
                    "question_id": a.question_id,
                    "selected_option_id": a.selected_option_id,
                    "other_text": a.other_text,
                }
                for a in self.resolved_answers
            ],
            "ready_to_execute": self.ready_to_execute,
            "execution_brief": self.execution_brief,
            "risk_level": self.risk_level,
            "confidence_score": self.confidence_score,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "PlanSessionResult":
        return cls(
            instruction=str(payload.get("instruction", "")),
            task_summary=str(payload.get("task_summary", "")),
            candidate_files=[str(v) for v in payload.get("candidate_files", [])],  # type: ignore[arg-type]
            assumptions=[str(v) for v in payload.get("assumptions", [])],  # type: ignore[arg-type]
            recommended_steps=[str(v) for v in payload.get("recommended_steps", [])],  # type: ignore[arg-type]
            open_questions=[
                PlanQuestion(
                    id=str(item.get("id", "")),
                    question=str(item.get("question", "")),
                    rationale=str(item.get("rationale", "")),
                    options=[
                        PlanOption(
                            id=str(opt.get("id", "")),
                            label=str(opt.get("label", "")),
                            description=str(opt.get("description", "")),
                            is_other=bool(opt.get("is_other", False)),
                        )
                        for opt in item.get("options", [])
                    ],
                )
                for item in payload.get("open_questions", [])  # type: ignore[arg-type]
            ],
            resolved_answers=[
                PlanAnswer(
                    question_id=str(item.get("question_id", "")),
                    selected_option_id=str(item.get("selected_option_id", "")),
                    other_text=str(item.get("other_text", "")),
                )
                for item in payload.get("resolved_answers", [])  # type: ignore[arg-type]
            ],
            ready_to_execute=bool(payload.get("ready_to_execute", False)),
            execution_brief=str(payload.get("execution_brief", "")),
            risk_level=str(payload.get("risk_level", "medium")),
            confidence_score=float(payload.get("confidence_score", 0.5)),
        )
