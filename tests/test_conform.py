"""The "what will change" list, and the job that carries it into FFmpeg."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from framecheck.app.media.conform import (
    build_frame_rate_mode_args,
    build_job,
    detect_frame_rate_mode,
    plan_conform,
)
from framecheck.app.models.export_job import LoudnessResult
from framecheck.app.models.media_info import AudioStreamInfo, MediaInfo, VideoStreamInfo
from framecheck.app.models.media_time import FrameRate, MediaTime
from framecheck.app.models.profile import AudioTarget, Profile, TargetSpec
from framecheck.app.models.trim import Fit, TargetDuration, TrimRange
from framecheck.app.utils.paths import OutputDestination

from conftest import requires_fixtures

R23976 = FrameRate(Fraction(24000, 1001))
R2997 = FrameRate(Fraction(30000, 1001))

CTV_TARGET = TargetSpec(
    container="mp4",
    video_codec="h264",
    width=1920,
    height=1080,
    allowed_frame_rates=(
        Fraction(24000, 1001),
        Fraction(25),
        Fraction(30000, 1001),
    ),
    preferred_frame_rate=Fraction(30000, 1001),
    video_bitrate_mbps=20.0,
    audio=AudioTarget(loudness_lkfs=-24.0),
)

CTV_PROFILE = Profile(id="ctv-generic", name="Connected TV", target=CTV_TARGET)


def prores_4k() -> MediaInfo:
    """A 4096x2160 ProRes 422 master at 23.976 with 5.1 48 kHz audio."""
    return MediaInfo(
        path=Path("C:/media/master.mov"),
        container_format="mov,mp4,m4a,3gp,3g2,mj2",
        duration_seconds=Fraction(60),
        video=VideoStreamInfo(
            index=0,
            codec="prores",
            width=4096,
            height=2160,
            r_frame_rate=R23976,
            avg_frame_rate=R23976,
            bitrate_bps=180_000_000,
        ),
        audio=AudioStreamInfo(
            index=1,
            codec="pcm_s24le",
            sample_rate_hz=48000,
            channels=6,
            channel_layout="5.1",
            bitrate_bps=6_912_000,
        ),
    )


def on_spec() -> MediaInfo:
    """A file that already matches the CTV target in every respect."""
    return MediaInfo(
        path=Path("C:/media/already.mp4"),
        container_format="mov,mp4,m4a,3gp,3g2,mj2",
        duration_seconds=Fraction(30),
        video=VideoStreamInfo(
            index=0,
            codec="h264",
            width=1920,
            height=1080,
            r_frame_rate=R23976,
            avg_frame_rate=R23976,
            bitrate_bps=20_000_000,
        ),
        audio=AudioStreamInfo(
            index=1,
            codec="aac",
            sample_rate_hz=48000,
            channels=2,
            bitrate_bps=320_000,
        ),
    )


def labels(actions) -> set[str]:
    return {a.label for a in actions}


def find(actions, label: str):
    return next(a for a in actions if a.label == label)


def test_prores_4k_to_ctv_lists_every_real_change():
    actions = plan_conform(prores_4k(), CTV_TARGET)
    assert labels(actions) >= {
        "Video codec",
        "Resolution",
        "Video bitrate",
        "Audio codec",
        "Audio channels",
        "Audio bitrate",
    }
    # Already in an MP4-family container, and 23.976 is an allowed rate.
    assert "Container" not in labels(actions)
    assert "Frame rate" not in labels(actions)


def test_aspect_change_is_padded_not_cropped():
    resolution = find(plan_conform(prores_4k(), CTV_TARGET), "Resolution")
    assert resolution.to_value == "1920x1080"
    # The wording may change; what must never change is that the reason states
    # padding happens and cropping does not.
    assert "padded" in resolution.reason
    assert "never cropped" in resolution.reason
    assert "crop" not in resolution.reason.replace("never cropped", "")
    assert resolution.affects_picture_or_sound


def test_on_spec_source_produces_no_actions():
    assert plan_conform(on_spec(), CTV_TARGET) == ()


def test_disallowed_rate_reports_a_conversion():
    info = on_spec()
    target = TargetSpec(
        **{**CTV_TARGET.__dict__, "allowed_frame_rates": (Fraction(30000, 1001),)}
    )
    action = find(plan_conform(info, target), "Frame rate")
    assert action.from_value == "23.976"
    assert action.to_value == "29.97 CFR"
    assert action.affects_picture_or_sound
    assert "interpolated" in action.reason


def test_variable_frame_rate_is_made_constant():
    base = on_spec()
    video = VideoStreamInfo(
        **{**base.video.__dict__, "avg_frame_rate": FrameRate(Fraction(20))}
    )
    info = MediaInfo(**{**base.__dict__, "video": video})
    action = find(plan_conform(info, CTV_TARGET), "Frame rate")
    assert action.from_value.startswith("variable")
    assert action.to_value.endswith("CFR")


def test_loudness_delta_maths():
    measured = LoudnessResult(integrated_lufs=-19.8)
    action = find(
        plan_conform(on_spec(), CTV_TARGET, loudness=measured, normalize=True),
        "Loudness",
    )
    assert action.from_value == "-19.8 LUFS"
    assert action.to_value == "-24 LKFS"
    assert "-4.2 dB" in action.reason
    assert action.affects_picture_or_sound


def test_loudness_already_on_target_is_not_an_action():
    measured = LoudnessResult(integrated_lufs=-24.02)
    actions = plan_conform(on_spec(), CTV_TARGET, loudness=measured, normalize=True)
    assert "Loudness" not in labels(actions)


def test_unmeasured_loudness_is_flagged_as_approximate():
    action = find(
        plan_conform(on_spec(), CTV_TARGET, normalize=True), "Loudness"
    )
    assert "approximation" in action.reason


def test_missing_audio_is_stated_not_fixed():
    base = on_spec()
    info = MediaInfo(**{**base.__dict__, "audio": None})
    action = find(plan_conform(info, CTV_TARGET), "Audio")
    assert "will not invent silence" in action.reason
    assert action.affects_picture_or_sound


def test_trim_reports_both_durations_in_frames():
    info = on_spec()
    trim = TrimRange(MediaTime(48, R23976), MediaTime(768, R23976))
    action = find(plan_conform(info, CTV_TARGET, trim=trim), "Trim")
    assert "720 frames" in action.to_value
    assert "OUT exclusive" in action.reason


def test_full_range_trim_is_not_an_action():
    info = on_spec()
    trim = TrimRange.full(info.duration)
    assert "Trim" not in labels(plan_conform(info, CTV_TARGET, trim=trim))


def test_container_change_is_reported():
    base = prores_4k()
    info = MediaInfo(**{**base.__dict__, "container_format": "matroska,webm"})
    assert find(plan_conform(info, CTV_TARGET), "Container").to_value == "mp4"


def test_build_job_names_the_output_and_carries_the_actions(tmp_path):
    info = prores_4k()
    job = build_job(
        info, CTV_PROFILE, destination=OutputDestination.custom(tmp_path)
    )
    assert job.output_path == tmp_path / "FC_CTV-GENERIC-master.mp4"
    assert job.output_path != job.source_path
    assert job.target is CTV_TARGET
    assert job.actions == plan_conform(info, CTV_TARGET)


def test_build_job_expected_duration_follows_the_trim():
    info = on_spec()
    trim = TrimRange(MediaTime(0, R2997), MediaTime(900, R2997))
    job = build_job(info, CTV_PROFILE, trim=trim)
    assert job.expected_duration_seconds == Fraction(30030, 1000)
    assert job.expected_frame_count == 900


def test_empty_trim_is_dropped():
    info = on_spec()
    empty = TrimRange(MediaTime(10, R2997), MediaTime(10, R2997))
    assert build_job(info, CTV_PROFILE, trim=empty).trim is None


def test_frame_rate_mode_args_sample_a_bounded_window():
    args = build_frame_rate_mode_args(Path("C:/media/x.mp4"))
    assert "packet=pts_time" in args
    assert args[args.index("-read_intervals") + 1] == "%+#300"
    assert args[-1] == str(Path("C:/media/x.mp4"))


@requires_fixtures
@pytest.mark.parametrize("name", ["clip_23976.mp4", "clip_25_pal.mp4"])
def test_detect_frame_rate_mode_on_real_cfr_fixtures(fixtures_dir, name):
    assert detect_frame_rate_mode(fixtures_dir / name) == "cfr"


# --- exact duration --------------------------------------------------------

SOCIAL_PROFILE = Profile(
    id="social",
    name="Social",
    target=TargetSpec(
        allowed_frame_rates=(Fraction(30000, 1001), Fraction(30)),
        preferred_frame_rate=Fraction(30),
    ),
)


def at_2997() -> MediaInfo:
    base = on_spec()
    video = VideoStreamInfo(
        **{**base.video.__dict__, "r_frame_rate": R2997, "avg_frame_rate": R2997}
    )
    return MediaInfo(**{**base.__dict__, "video": video})


def fifteen(fit: Fit = Fit.SPEED) -> tuple[TrimRange, TargetDuration]:
    target = TargetDuration.of(15, fit=fit)
    return TrimRange.for_target(MediaTime(0, R2997), target), target


def test_a_15_preset_at_2997_exports_exactly_15_seconds_in_sync():
    trim, target = fifteen()
    job = build_job(at_2997(), SOCIAL_PROFILE, trim=trim, target_duration=target)
    assert job.exact_cut is not None
    assert job.expected_duration_seconds == 15
    assert job.expected_frame_count == 450
    assert "sped up 0.1%, in sync" in find(job.actions, "Exact duration").reason
    assert "Frame rate" not in labels(job.actions)


def test_hold_fit_says_where_the_frame_is_held():
    trim, target = fifteen(Fit.HOLD_END)
    job = build_job(at_2997(), SOCIAL_PROFILE, trim=trim, target_duration=target)
    assert "1 frame held at the end" in find(job.actions, "Exact duration").reason


def test_refused_whole_rate_falls_back_under_the_slot_never_over():
    trim, target = fifteen()
    assert trim.frame_count == 450  # 15.015 s at 29.97: over, if sent as-is
    job = build_job(at_2997(), CTV_PROFILE, trim=trim, target_duration=target)
    assert job.exact_cut is None
    assert job.expected_frame_count == 449
    assert job.expected_duration_seconds < 15


def test_a_nudged_out_point_is_the_users_cut_not_the_presets():
    trim, target = fifteen()
    nudged = trim.with_out(trim.out_point.offset_frames(-1))
    job = build_job(at_2997(), SOCIAL_PROFILE, trim=nudged, target_duration=target)
    assert job.exact_cut is None
    assert job.trim == nudged


def test_a_destination_that_converts_the_rate_gets_an_exact_real_time_cut():
    """59.94 to a 30 fps destination: no speed-up, just cut 15.000 s."""
    r5994 = FrameRate(Fraction(60000, 1001))
    base = on_spec()
    video = VideoStreamInfo(
        **{**base.video.__dict__, "r_frame_rate": r5994, "avg_frame_rate": r5994}
    )
    info = MediaInfo(**{**base.__dict__, "video": video})
    target = TargetDuration.of(15)
    trim = TrimRange.for_target(MediaTime(0, r5994), target)
    only_30 = Profile(
        id="thirty",
        name="Thirty",
        target=TargetSpec(allowed_frame_rates=(Fraction(30),), preferred_frame_rate=Fraction(30)),
    )
    job = build_job(info, only_30, trim=trim, target_duration=target)
    assert job.exact_cut.fit is Fit.CONVERT
    assert job.expected_duration_seconds == 15
    assert job.expected_frame_count == 450
    assert job.exact_cut.speed == 1
    assert {"Frame rate", "Exact duration"} <= labels(job.actions)
