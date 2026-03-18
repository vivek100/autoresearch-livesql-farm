from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AnswerEnvelope:
    raw: Any
    normalized: Any
    kind: str


@dataclass
class CandidateRunResult:
    question: str
    db_id: str
    status: str
    answer: AnswerEnvelope
    final_sql: str | None
    steps_used: int
    artifacts: dict[str, Any] = field(default_factory=dict)
    model_name: str | None = None
    runtime: str = "langgraph.prebuilt.create_react_agent"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["answer"] = asdict(self.answer)
        return data

