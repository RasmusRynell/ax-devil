"""Tests for logging configuration helpers."""

from ax_devil.modules.settings.qt_logging import QT_ACCESSIBILITY_ATSPI_LOGGING_RULE, qt_logging_rules_with_defaults


def test_qt_logging_rules_adds_atspi_filter_when_empty() -> None:
    """Default Qt logging rules silence noisy AT-SPI accessibility messages."""
    assert qt_logging_rules_with_defaults(None) == QT_ACCESSIBILITY_ATSPI_LOGGING_RULE


def test_qt_logging_rules_preserves_existing_rules() -> None:
    """Existing user Qt logging rules are kept when adding the AT-SPI filter."""
    assert (
        qt_logging_rules_with_defaults("qt.network.ssl.warning=false")
        == f"qt.network.ssl.warning=false\n{QT_ACCESSIBILITY_ATSPI_LOGGING_RULE}"
    )


def test_qt_logging_rules_do_not_duplicate_atspi_filter() -> None:
    """User-provided AT-SPI rules take precedence over the default filter."""
    assert (
        qt_logging_rules_with_defaults("qt.accessibility.atspi.warning=false") == "qt.accessibility.atspi.warning=false"
    )
