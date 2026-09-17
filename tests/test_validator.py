"""Validating hand-built MediaInfo against the shipped profiles.

Every assertion here is about a status a user will act on, so they are exact:
"not PASS" is not good enough when the difference between WARNING and FAIL is
the difference between shipping and re-exporting.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from framecheck.app.models.export_job import LoudnessResult
from framecheck.app.models.media_info import AudioStreamInfo, MediaInfo, VideoStreamInfo
from framecheck.app.models.media_time import FrameRate
from framecheck.app.models.profile import CheckStatus, Profile, Rule, Severity
from framecheck.app.models.validation_result import CheckResult, ValidationReport
from framecheck.app.profiles.loader import ProfileLoader
from framecheck.app.profiles.validator import validate

LOADER = ProfileLoader()
CTV = LOADER.get("streaming_ctv")

RATE_2997 = FrameRate(Fraction(30000, 1001))


def make_info(
    *,
    container: str = "mov,mp4,m4a,3gp,3g2,mj2",
    codec: str = "h264",
    width: int = 1920,
    height: int = 1080,
    display_aspect_ratio: str | None = None,
    pixel_format: str = "yuv420p",
    r_rate: FrameRate | None = RATE_2997,
    avg_rate: FrameRate | None = RATE_2997,
    video_bitrate_bps: int | None = 20_000_000,
    field_order: str | None = None,
    audio: bool = True,
    audio_codec: str = "aac",
    channels: int = 2,
    sample_rate_hz: int = 48000,
    audio_bitrate_bps: int | None = 320_000,
    size_bytes: int = 80 * 1_048_576,
    duration: Fraction = Fraction(30),
) -> MediaInfo:
    """A conforming CTV file, with one thing broken per keyword argument."""
    return MediaInfo(
        path=Path("clip.mp4"),
        container_format=container,
        duration_seconds=duration,
        size_bytes=size_bytes,
        bitrate_bps=21_000_000,
        video=VideoStreamInfo(
            index=0,
            codec=codec,
            profile="High",
            width=width,
            height=height,
            display_aspect_ratio=display_aspect_ratio,
            pixel_format=pixel_format,
            r_frame_rate=r_rate,
            avg_frame_rate=avg_rate,
            bitrate_bps=video_bitrate_bps,
            field_order=field_order,
        ),
        audio=(
            AudioStreamInfo(
                index=1,
                codec=audio_codec,
                sample_rate_hz=sample_rate_hz,
                channels=channels,
                bitrate_bps=audio_bitrate_bps,
            )
            if audio
            else None
        ),
    )


def status_of(report: ValidationReport, field: str) -> CheckStatus:
    return next(c.status for c in report.checks if c.field == field)


def check_for(report: ValidationReport, field: str) -> CheckResult:
    return next(c for c in report.checks if c.field == field)


# --------------------------------------------------------------------------
# The happy path


def test_compliant_file_leaves_only_the_manual_checks() -> None:
    report = validate(make_info(), CTV, LoudnessResult(integrated_lufs=-24.0))
    not_passing = [c for c in report.checks if c.status is not CheckStatus.PASS]

    assert [c.field for c in not_passing] == ["review.disclaimer", "review.slate"]
    assert all(c.status is CheckStatus.MANUAL_REVIEW for c in not_passing)
    assert report.status is CheckStatus.MANUAL_REVIEW
    # A file nobody has eyeballed is not a clean file.
    assert report.passed is False


def test_manual_checks_never_pass_or_fail() -> None:
    report = validate(make_info(), CTV, LoudnessResult(integrated_lufs=-24.0))
    for check in report.checks:
        if check.field.startswith("review."):
            assert check.status is CheckStatus.MANUAL_REVIEW
            assert check.fixable is False
            assert check.guidance


# --------------------------------------------------------------------------
# Preferences are warnings, never failures


def test_low_audio_bitrate_warns_rather_than_fails() -> None:
    report = validate(make_info(audio_bitrate_bps=128_000), CTV)
    check = check_for(report, "audio.bitrate_kbps")

    assert check.status is CheckStatus.WARNING
    assert check.fixable is True
    assert check.fix_description == "Re-encode audio at 320 kbps"
    assert "192" in check.expected and "320" in check.expected


def test_bitrate_inside_the_band_passes_even_when_not_preferred() -> None:
    report = validate(make_info(video_bitrate_bps=16_000_000), CTV)
    assert status_of(report, "video.bitrate_mbps") is CheckStatus.PASS


def test_bitrate_outside_the_band_takes_the_rule_severity() -> None:
    report = validate(make_info(video_bitrate_bps=40_000_000), CTV)
    check = check_for(report, "video.bitrate_mbps")
    assert check.status is CheckStatus.WARNING
    assert CTV.rule_for("video.bitrate_mbps").severity is Severity.WARNING


def test_wrong_codec_fails_and_offers_a_concrete_fix() -> None:
    report = validate(make_info(codec="prores"), CTV)
    check = check_for(report, "video.codec")
    assert check.status is CheckStatus.FAIL
    assert check.fixable is True
    assert check.fix_description == "Re-encode to H.264"


def test_wrong_sample_rate_fix_reads_in_kilohertz() -> None:
    report = validate(make_info(sample_rate_hz=44100), CTV)
    check = check_for(report, "audio.sample_rate_hz")
    assert check.status is CheckStatus.WARNING
    assert check.fix_description == "Resample audio to 48 kHz"


# --------------------------------------------------------------------------
# Frame rate is rational, not float


def test_2997_matches_an_allowed_2997() -> None:
    report = validate(make_info(), CTV)
    assert status_of(report, "video.frame_rate") is CheckStatus.PASS
    assert check_for(report, "video.frame_rate").actual == "29.97 fps"


def test_5994_is_not_an_allowed_ctv_rate() -> None:
    rate = FrameRate(Fraction(60000, 1001))
    report = validate(make_info(r_rate=rate, avg_rate=rate), CTV)
    check = check_for(report, "video.frame_rate")
    assert check.status is CheckStatus.FAIL
    assert check.fix_description == "Convert frame rate to 23.976 fps"


def test_max_frame_rate_bound_compares_numerically() -> None:
    reels = LOADER.get("meta_reels")
    slow = validate(make_info(width=1080, height=1920), reels)
    assert status_of(slow, "video.frame_rate") is CheckStatus.PASS

    rate = FrameRate(Fraction(60, 1))
    fast = validate(make_info(width=1080, height=1920, r_rate=rate, avg_rate=rate), reels)
    assert status_of(fast, "video.frame_rate") is CheckStatus.FAIL


# --------------------------------------------------------------------------
# Frame rate mode, scan type


def test_variable_frame_rate_fails_with_heuristic_guidance() -> None:
    report = validate(
        make_info(r_rate=FrameRate(Fraction(60, 1)), avg_rate=FrameRate(Fraction(30, 1))),
        CTV,
    )
    check = check_for(report, "video.frame_rate_mode")

    assert check.status is CheckStatus.FAIL
    assert check.actual == "VFR"
    assert "heuristic" in check.guidance
    assert "packet analysis" in check.guidance
    assert check.fixable is True
    assert check.fix_description == "Re-encode at a constant frame rate"


def test_missing_frame_rate_is_manual_not_pass() -> None:
    report = validate(make_info(r_rate=None, avg_rate=None), CTV)
    assert status_of(report, "video.frame_rate_mode") is CheckStatus.MANUAL_REVIEW
    assert status_of(report, "video.frame_rate") is CheckStatus.MANUAL_REVIEW


def test_absent_field_order_means_progressive() -> None:
    assert status_of(validate(make_info(), CTV), "video.scan_type") is CheckStatus.PASS


def test_interlaced_fails_and_is_fixable() -> None:
    check = check_for(validate(make_info(field_order="tt"), CTV), "video.scan_type")
    assert check.status is CheckStatus.FAIL
    assert check.fix_description == "Deinterlace to progressive"


# --------------------------------------------------------------------------
# Aspect ratio


def test_vertical_source_against_ctv_is_a_failure_no_export_can_fix() -> None:
    report = validate(make_info(width=1080, height=1920), CTV)
    check = check_for(report, "video.aspect_ratio")

    assert check.status is CheckStatus.FAIL
    assert check.actual == "9:16"
    assert check.fixable is False
    assert check.fix_description is None
    assert "composition" in check.guidance
    assert check in report.unfixable


def test_declared_dar_and_derived_ratio_agree_within_a_percent() -> None:
    report = validate(make_info(display_aspect_ratio="1.778"), CTV)
    assert status_of(report, "video.aspect_ratio") is CheckStatus.PASS


def test_aspect_is_never_fixable_even_when_the_rule_says_so() -> None:
    profile = Profile(
        id="lies",
        name="Lies",
        rules=(Rule(field="video.aspect_ratio", label="Aspect", equals="16:9", fixable=True),),
    )
    check = validate(make_info(width=1080, height=1920), profile).checks[0]
    assert check.fixable is False


# --------------------------------------------------------------------------
# Things that cannot be determined


def test_a_file_with_no_audio_is_not_applicable_never_pass() -> None:
    report = validate(make_info(audio=False), CTV)
    audio_checks = [c for c in report.checks if c.field.startswith("audio.")]

    assert audio_checks
    assert all(c.status is CheckStatus.NOT_APPLICABLE for c in audio_checks)
    assert all(c.actual == "No audio stream" for c in audio_checks)
    # NOT_APPLICABLE must not drag the report down either.
    assert report.status is CheckStatus.MANUAL_REVIEW


def test_unmeasured_loudness_is_manual_review() -> None:
    report = validate(make_info(), CTV, loudness=None)
    check = check_for(report, "audio.loudness_lkfs")
    assert check.status is CheckStatus.MANUAL_REVIEW
    assert check.actual == "Not reported"


def test_absent_video_bitrate_is_manual_review() -> None:
    report = validate(make_info(video_bitrate_bps=None), CTV)
    assert status_of(report, "video.bitrate_mbps") is CheckStatus.MANUAL_REVIEW


# --------------------------------------------------------------------------
# Loudness tolerance band


@pytest.mark.parametrize("lkfs", [-24.0, -22.5, -25.9])
def test_loudness_inside_the_band_passes(lkfs: float) -> None:
    report = validate(make_info(), CTV, LoudnessResult(integrated_lufs=lkfs))
    assert status_of(report, "audio.loudness_lkfs") is CheckStatus.PASS


def test_loudness_outside_the_band_warns_with_a_fix() -> None:
    report = validate(make_info(), CTV, LoudnessResult(integrated_lufs=-18.1))
    check = check_for(report, "audio.loudness_lkfs")

    assert check.status is CheckStatus.WARNING
    assert check.actual == "-18.1 LKFS"
    assert check.fixable is True
    assert check.fix_description == "Normalize to -24 LKFS"


# --------------------------------------------------------------------------
# Container matching


def test_mp4_matches_the_multi_name_container_ffprobe_reports() -> None:
    assert status_of(validate(make_info(), CTV), "container") is CheckStatus.PASS


def test_a_real_quicktime_file_fails_the_container_rule() -> None:
    check = check_for(validate(make_info(container="mov"), CTV), "container")
    assert check.status is CheckStatus.FAIL
    assert check.fix_description == "Rewrap to MP4"


# --------------------------------------------------------------------------
# Other shipped profiles


def test_file_size_over_the_online_video_cap_warns() -> None:
    profile = LOADER.get("online_video")
    report = validate(make_info(size_bytes=300 * 1_048_576), profile)
    check = check_for(report, "file.size_mb")
    assert check.status is CheckStatus.WARNING
    assert check.actual == "300.0 MB"


def test_youtube_accepts_720p_as_a_minimum() -> None:
    profile = LOADER.get("youtube")
    assert status_of(validate(make_info(width=1280, height=720), profile), "video.height") is (
        CheckStatus.PASS
    )
    assert status_of(validate(make_info(width=640, height=360), profile), "video.height") is (
        CheckStatus.FAIL
    )


def test_youtube_does_not_constrain_aspect() -> None:
    profile = LOADER.get("youtube")
    report = validate(make_info(width=1080, height=1920), profile)
    assert not [c for c in report.checks if c.field == "video.aspect_ratio"]


def test_reddit_accepts_several_aspect_ratios_but_not_prores() -> None:
    profile = LOADER.get("reddit")
    for width, height in ((1080, 1080), (1080, 1350), (1080, 1920), (1920, 1080)):
        report = validate(make_info(width=width, height=height), profile)
        assert status_of(report, "video.aspect_ratio") is CheckStatus.PASS

    prores = validate(make_info(codec="prores", container="mov"), profile)
    assert status_of(prores, "video.codec") is CheckStatus.FAIL


# --------------------------------------------------------------------------
# Report rollup


def _result(status: CheckStatus, field: str = "x") -> CheckResult:
    return CheckResult(label=field, status=status, actual="a", expected="b", field=field)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((CheckStatus.PASS, CheckStatus.NOT_APPLICABLE), CheckStatus.PASS),
        ((CheckStatus.PASS, CheckStatus.MANUAL_REVIEW), CheckStatus.MANUAL_REVIEW),
        ((CheckStatus.MANUAL_REVIEW, CheckStatus.WARNING), CheckStatus.WARNING),
        ((CheckStatus.WARNING, CheckStatus.FAIL), CheckStatus.FAIL),
        ((CheckStatus.FAIL, CheckStatus.MANUAL_REVIEW), CheckStatus.FAIL),
    ],
)
def test_report_status_is_the_worst_check(statuses, expected) -> None:
    report = ValidationReport("p", "P", tuple(_result(s) for s in statuses))
    assert report.status is expected


def test_manual_review_alone_means_not_passed() -> None:
    checks = (_result(CheckStatus.PASS), _result(CheckStatus.MANUAL_REVIEW))
    report = ValidationReport("p", "P", checks)
    assert report.status is CheckStatus.MANUAL_REVIEW
    assert report.passed is False


def test_all_pass_means_passed() -> None:
    checks = (_result(CheckStatus.PASS), _result(CheckStatus.NOT_APPLICABLE))
    assert ValidationReport("p", "P", checks).passed is True


def test_fixable_and_unfixable_split_the_problems() -> None:
    report = validate(make_info(codec="prores", width=1080, height=1920), CTV)
    assert check_for(report, "video.codec") in report.fixable
    assert check_for(report, "video.aspect_ratio") in report.unfixable
