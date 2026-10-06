"""Regression coverage for Qt widget ownership between pytest test cases."""

import subprocess
import sys
from pathlib import Path
from textwrap import dedent


def test_qt_widgets_are_destroyed_between_tests(tmp_path: Path) -> None:
    """A closed widget must not survive until a later test's worker collects it."""
    (tmp_path / "conftest.py").write_text(
        "from tests.conftest import pytest_runtest_teardown\n",
        encoding="utf-8",
    )
    (tmp_path / "test_widget_lifetime.py").write_text(
        dedent(
            '''\
            from PySide6.QtWidgets import QWidget
            from pytestqt.qtbot import QtBot
            from shiboken6 import isValid

            widgets: list[QWidget] = []


            def test_create_widget(qtbot: QtBot) -> None:
                """Keep a Python wrapper alive after pytest-qt closes its widget."""
                widget = QWidget()
                qtbot.addWidget(widget)
                widgets.append(widget)
                widget.show()


            def test_previous_widget_is_destroyed() -> None:
                """The native widget must already be deleted before this test."""
                assert len(widgets) == 1
                assert not isValid(widgets[0])
            '''
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
