"""What a decoder or resolver could not load, in the words the user sees on the lane warning."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

_MAX_REASONS = 3
_MAX_REASON_CHARS = 200


@dataclass(frozen=True, slots=True)
class LoadWarning:
    """What a decoder or resolver could not load, why, and what the user loses."""

    problem: str  # "Skipped 1 of 3 rows"
    reasons: tuple[str, ...]  # At most three examples, each naming where: "line 2: expected 7, 9 or 10 columns, got 5"
    consequence: str  # "Objects on skipped rows are not shown."
    more_reasons: bool = False  # Whether further examples were left out.

    @classmethod
    def skipped(cls, noun: str, total: int, reasons: Sequence[str], consequence: str) -> LoadWarning | None:
        """Return the warning for *reasons*, one per skipped item of *total* items called *noun*, or None if none."""
        if not reasons:
            return None
        examples = tuple(
            reason if len(reason) <= _MAX_REASON_CHARS else f"{reason[: _MAX_REASON_CHARS - 1]}…"
            for reason in reasons[:_MAX_REASONS]
        )
        return cls(f"Skipped {len(reasons)} of {total} {noun}", examples, consequence, len(reasons) > _MAX_REASONS)

    @property
    def text(self) -> str:
        """Return the warning as lines: the problem, its indented reasons, then the consequence."""
        lines = [self.problem, *(f"  {reason}" for reason in self.reasons)]
        if self.more_reasons:
            lines.append("  …")
        lines.append(self.consequence)
        return "\n".join(lines)

    def to_metadata(self) -> dict[str, Any]:
        """Return the JSON-compatible form persisted with cached scenes."""
        return {
            "problem": self.problem,
            "reasons": list(self.reasons),
            "consequence": self.consequence,
            "more_reasons": self.more_reasons,
        }

    @classmethod
    def from_metadata(cls, raw: object) -> LoadWarning | None:
        """Return the warning persisted by `to_metadata`, or None if *raw* is not one."""
        if not isinstance(raw, dict):
            return None
        problem, reasons, consequence = raw.get("problem"), raw.get("reasons"), raw.get("consequence")
        more_reasons = raw.get("more_reasons")
        if (
            not isinstance(problem, str)
            or not isinstance(consequence, str)
            or not isinstance(reasons, list)
            or not all(isinstance(reason, str) for reason in reasons)
            or not isinstance(more_reasons, bool)
        ):
            return None
        return cls(problem, tuple(reasons), consequence, more_reasons)
