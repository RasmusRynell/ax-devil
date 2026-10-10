"""Default Qt logging rules. Kept apart from ``logging_config`` so that module stays Qt-free."""

from __future__ import annotations

import os

from PySide6.QtCore import QLoggingCategory

QT_ACCESSIBILITY_ATSPI_CATEGORY = "qt.accessibility.atspi"
QT_ACCESSIBILITY_ATSPI_LOGGING_RULE = f"{QT_ACCESSIBILITY_ATSPI_CATEGORY}=false"


def qt_logging_rules_with_defaults(existing_rules: str | None) -> str:
    """Add ax-devil's default Qt logging rules without overriding user rules."""
    rules = (existing_rules or "").strip()
    if QT_ACCESSIBILITY_ATSPI_CATEGORY in rules:
        return rules
    if not rules:
        return QT_ACCESSIBILITY_ATSPI_LOGGING_RULE
    return f"{rules}\n{QT_ACCESSIBILITY_ATSPI_LOGGING_RULE}"


def setup_qt_logging() -> None:
    """Install default Qt logging rules before QApplication starts."""
    rules = qt_logging_rules_with_defaults(os.environ.get("QT_LOGGING_RULES"))
    os.environ["QT_LOGGING_RULES"] = rules
    QLoggingCategory.setFilterRules(rules)
