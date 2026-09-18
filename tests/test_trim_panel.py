"""The trim panel's stupid-proofing, driven the way a user would, offscreen."""

from __future__ import annotations

import os
from fractions import Fraction

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from framecheck.app.models.media_time import FrameRate, MediaTime  # noqa: E402
from framecheck.app.models.trim import Fit  # noqa: E402
from framecheck.app.ui.trim_panel import TrimPanel  # noqa: E402

R2997 = FrameRate(Fraction(30000, 1001))
R23976 = FrameRate(Fraction(24000, 1001))


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def panel(frames: int, rate: FrameRate) -> TrimPanel:
    p = TrimPanel()
    p.set_source(MediaTime(frames, rate), rate)
    return p


def click_length(p: TrimPanel, seconds: int) -> None:
    next(b for b, s in p._segments if s == seconds).click()


def test_an_off_length_cut_offers_the_one_click_fix() -> None:
    p = panel(1500, R2997)
    p.set_trim(p.trim().with_in(MediaTime(30, R2997)).with_out(MediaTime(882, R2997)))
    assert "not a standard length" in p.banner.headline.text()
    p.banner.fix_button.click()
    assert p.target().seconds == 30 and p.trim().frame_count == 900
    assert p.banner.headline.text() == "Export will be exactly 30.000 s"


def test_choosing_a_card_recuts_from_the_same_in_point() -> None:
    p = panel(1500, R2997)
    click_length(p, 30)
    p.cards[Fit.HOLD_END].radio.click()
    assert (p.target().fit, p.trim().frame_count) == (Fit.HOLD_END, 899)
    p.cards[Fit.SPEED].radio.click()
    assert p.trim().frame_count == 900


def test_dragging_out_drops_the_preset() -> None:
    p = panel(1500, R2997)
    click_length(p, 15)
    p.set_trim(p.trim().with_out(p.trim().out_point.offset_frames(-3)))
    assert p.target() is None
    assert "not a standard length" in p.banner.headline.text()


def test_a_master_a_frame_short_holds_the_end_by_itself() -> None:
    p = panel(719, R23976)  # 29.988 s
    click_length(p, 30)
    assert p.target().fit is Fit.HOLD_END
    assert not p.cards[Fit.SPEED].radio.isEnabled()
    assert p.fit() is Fit.SPEED  # the preference survives the fallback


def test_footage_too_short_for_any_fit_says_so_and_suggests_one_that_fits() -> None:
    p = panel(600, R2997)  # 20 s
    click_length(p, 30)
    assert p.target() is None
    assert "too short for :30" in p.banner.headline.text()
    assert p.banner.fix_button.text() == "Make it :15"
