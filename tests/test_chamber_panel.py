"""Smoke tests for the Chamber tab: it builds, and it reacts to a picked journey.

Skipped where Qt cannot start (no display libraries, no PySide6) -- the engine
tests above cover the behaviour that matters; this covers the wiring.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyqtgraph")

from PySide6 import QtWidgets  # noqa: E402

from local_mind_monitor.chamber import journeys as J  # noqa: E402
from local_mind_monitor.gui.chamber_panel import ChamberPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def panel(app):
    widget = ChamberPanel(device_getter=lambda: None)
    yield widget
    widget.shutdown()
    widget.deleteLater()


def test_panel_lists_every_built_in_journey(panel):
    assert panel.journey_box.count() >= len(J.BUILTIN)
    assert panel.journey.key == J.BUILTIN[0].key


def test_selecting_a_journey_updates_summary_mode_and_timeline(panel):
    index = [j.key for j in panel.journeys].index("open-room")
    panel.journey_box.setCurrentIndex(index)
    assert "isochronic" in panel.summary_lbl.text().lower() or panel._mode() == "isochronic"
    assert panel._mode() == "isochronic"          # the journey's own mode is honoured
    assert "speakers" in panel.hint_lbl.text().lower()
    xs, ys = panel.plan_curve.getData()
    assert len(xs) == len(ys) > 100
    assert max(ys) == pytest.approx(10.0, abs=0.1)


def test_caution_is_surfaced_for_journeys_that_carry_one(panel):
    index = [j.key for j in panel.journeys].index("deep-delta")
    panel.journey_box.setCurrentIndex(index)
    assert "driving" in panel.summary_lbl.text()


def test_binaural_journeys_say_headphones(panel):
    index = [j.key for j in panel.journeys].index("first-descent")
    panel.journey_box.setCurrentIndex(index)
    assert "headphones" in panel.hint_lbl.text().lower()


def test_no_tuning_offsets_before_any_session(panel):
    assert panel._learned_offsets() == {}


def test_volume_change_without_a_session_is_harmless(panel):
    panel.volume.setValue(30)
    assert panel.session is None
