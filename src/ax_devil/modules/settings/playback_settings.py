"""User-facing policy for the shared offline video cache allowance."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


def detect_available_memory_bytes() -> int | None:
    """Read Linux's available-RAM estimate, including reclaimable memory."""
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
                return available if available >= 0 else None
    except (OSError, ValueError, IndexError):
        return None
    return None


@dataclass(frozen=True)
class VideoCacheBudget:
    """Auto or a manual total cache allowance, independent of process RSS."""

    mib: int | None = None
    MIN_MIB: ClassVar[int] = 256
    MAX_MIB: ClassVar[int] = 1024 * 1024
    FALLBACK_MIB: ClassVar[int] = 1024
    LABEL: ClassVar[str] = "Memory for video caching"
    AUTO_LABEL: ClassVar[str] = "Auto (25% of available RAM)"
    MANUAL_LABEL: ClassVar[str] = "Manual"
    DESCRIPTION: ClassVar[str] = (
        "Shared across videos. Higher limits keep more frames ready for seeking. "
        "Other application memory is additional."
    )

    def __post_init__(self) -> None:
        if self.mib is not None and (type(self.mib) is not int or not self.MIN_MIB <= self.mib <= self.MAX_MIB):
            raise ValueError(f"Video cache must be Auto or an integer from {self.MIN_MIB} to {self.MAX_MIB} MiB")

    @classmethod
    def from_config(cls, value: object) -> "VideoCacheBudget":
        """Use Auto for missing or unsupported saved values."""
        if type(value) is int and cls.MIN_MIB <= value <= cls.MAX_MIB:
            return cls(value)
        return cls()

    @property
    def config_value(self) -> int | str:
        """Serialize Auto explicitly; manual allowances are stored in whole MiB."""
        return "auto" if self.mib is None else self.mib

    def resolve_bytes(self, available_memory_bytes: int | None) -> int:
        """Use a quarter of startup available RAM; fall back to 1 GiB if detection fails."""
        if self.mib is not None:
            return self.mib * 1024**2
        if available_memory_bytes is not None:
            return max(0, available_memory_bytes) // 4
        return self.FALLBACK_MIB * 1024**2
