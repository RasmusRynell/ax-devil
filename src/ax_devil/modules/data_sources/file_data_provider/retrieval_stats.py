"""Unique overlay lookup outcomes of one file provider, for diagnostics metrics and the debug log."""

from __future__ import annotations

from dataclasses import dataclass, field

from ax_devil.modules.diagnostics.metrics_store import metrics_enabled, set_metric
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class RetrievalStats:
    """Unique request outcomes for a single provider instance.

    Buckets contain timestamps observed with each outcome. A timestamp may
    occur in multiple buckets when lookup policy changes; their union is the
    set of unique requests.
    """

    requested: int = 0
    exact: set[int] = field(default_factory=set)
    resolved: set[int] = field(default_factory=set)
    misses: set[int] = field(default_factory=set)

    def record(self, timestamp: int, outcomes: set[int]) -> None:
        """Count a unique timestamp once, even when its outcome changes on a later lookup."""
        if timestamp not in self.exact and timestamp not in self.resolved and timestamp not in self.misses:
            self.requested += 1
        outcomes.add(timestamp)

    def push_metrics(self, instance_id: str, available: int) -> None:
        """Push current retrieval stats to the metrics store for live debugging."""
        if not metrics_enabled():
            return
        set_metric(instance_id, "Available", available)
        set_metric(instance_id, "Unique requested timestamps", self.requested)
        set_metric(instance_id, "Unique exact matches", len(self.exact))
        set_metric(instance_id, "Unique fallback matches", len(self.resolved))
        set_metric(instance_id, "Unique missing timestamps", len(self.misses))

    def log_summary(self, *, name: str, decoder_name: str, available: int, total_lines: int) -> None:
        """Log parse and retrieval completeness."""
        skipped_at_parse = total_lines - available
        dec = decoder_name

        total_hits = len(self.exact | self.resolved)
        logger.debug(
            f"{name} [{dec}] summary: "
            f"{available} available, {self.requested} unique requested timestamps "
            f"({total_hits} hits [{len(self.exact)} exact, {len(self.resolved)} resolved], "
            f"{len(self.misses)} misses)"
        )

        if skipped_at_parse > 0:
            logger.debug(
                f"{name} [{dec}] PARSE GAP: {skipped_at_parse}/{total_lines} payload lines did not produce a scene"
            )
