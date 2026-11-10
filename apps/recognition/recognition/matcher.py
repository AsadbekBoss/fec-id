"""1:N matching against the gallery of AUTHORISED employees, with conservative decisions.

Rules (all configurable; ARCHITECTURE §4.2-4.3):
* similarity = cosine of L2-normalised embeddings; an employee's score = max over their templates;
* MATCHED only if top1 >= threshold + uncertain_band AND top1 - best_other_employee >= margin;
* top1 in [threshold, threshold + uncertain_band), or margin too small -> AMBIGUOUS:
  stored for human review, NEVER auto-identified;
* top1 < threshold -> UNKNOWN.
Templates of a different model are never compared (embedding spaces are incompatible).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from recognition.types import Decision, MatchResult


@dataclass(frozen=True)
class MatchPolicy:
    threshold: float
    min_margin: float = 0.05
    uncertain_band: float = 0.03


@dataclass(frozen=True)
class Template:
    template_id: str
    employee_id: str
    model_id: str
    vector: np.ndarray  # L2-normalised float32


class Gallery:
    """In-memory gallery for ONE embedding model. Vectors exist only in process memory."""

    def __init__(self, model_id: str, dim: int) -> None:
        self.model_id, self.dim = model_id, dim
        # (employee ids, matrix) swapped as ONE object, so a reload while matching is safe.
        self._state: tuple[list[str], np.ndarray] = ([], np.zeros((0, dim), dtype=np.float32))

    def __len__(self) -> int:
        return len(self._state[0])

    @property
    def employees(self) -> int:
        return len(set(self._state[0]))

    def load(self, templates: list[Template]) -> int:
        """Replace content atomically. Templates of another model or wrong dimension are skipped."""
        usable = [t for t in templates if t.model_id == self.model_id and t.vector.shape == (self.dim,)]
        matrix = (
            np.vstack([t.vector / max(np.linalg.norm(t.vector), 1e-12) for t in usable]).astype(np.float32)
            if usable
            else np.zeros((0, self.dim), dtype=np.float32)
        )
        self._state = ([t.employee_id for t in usable], matrix)
        return len(usable)

    def scores(self, embedding: np.ndarray) -> list[tuple[str, float]]:
        """(employee_id, best score) sorted descending."""
        ids, matrix = self._state
        if not ids:
            return []
        sims = matrix @ embedding.astype(np.float32)
        best: dict[str, float] = {}
        for emp, s in zip(ids, sims.tolist(), strict=True):
            if s > best.get(emp, -2.0):
                best[emp] = s
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)


def decide(ranked: list[tuple[str, float]], policy: MatchPolicy) -> MatchResult:
    if not ranked:
        return MatchResult(Decision.UNKNOWN, None, None, None, None)
    cand, top1 = ranked[0]
    top2 = ranked[1][1] if len(ranked) > 1 else None
    if top1 < policy.threshold:
        return MatchResult(Decision.UNKNOWN, None, top1, top2, cand)
    margin_ok = top2 is None or (top1 - top2) >= policy.min_margin
    confident = top1 >= policy.threshold + policy.uncertain_band
    if margin_ok and confident:
        return MatchResult(Decision.MATCHED, cand, top1, top2, cand)
    return MatchResult(Decision.AMBIGUOUS, None, top1, top2, cand)
