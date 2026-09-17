"""The argv the encoder will actually run.

These assert on the exact list, not a joined string: a joined string hides
whether "1920:1080" arrived as one argument or two, and that distinction is the
difference between an export and a crash.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from framecheck.app.media.ffmpeg_builder import (
    build_command_text,
    build_export_args,
    build_loudnorm_filter,
    format_rate,
    format_seconds,
    resolve_frame_rate,
)
from framecheck.app.models.export_job import ExportJob, LoudnessResult
from framecheck.app.models.media_info import AudioStreamInfo, MediaInfo, VideoStreamInfo
from framecheck.app.models.media_time import FrameRate, MediaTime
from framecheck.app.models.profile import AudioTarget, FrameRateBehavior, TargetSpec
from framecheck.app.models.trim import TrimRange

R2997 = FrameRate(Fraction(30000, 1001))
R23976 = FrameRate(Fraction(24000, 1001))
R25 = FrameRate(Fraction(25))

# A captured loudnorm analysis block, values as FFmpeg prints them (strings).
MEASURED = {
    "input_i": "-19.83",
    "input_tp": "-4.12",
    "input_lra": "6.70",
    "input_thresh": "-30.21",
    "output_i": "-24.02",
    "output_tp": "-8.31",
    "output_lra": "6.60",
    "output_thresh": "-34.38",
    "normalization_type": "dynamic",
    "target_offset": "0.02",
}


def make_info(
    *,
    width: int = 1920,
    height: int = 1080,
    codec: str = "prores",
    rate: FrameRate = R23976,
    audio: bool = True,
    path: Path = Path("C:/media/source.mov"),
    duration: Fraction = Fraction(60),
) -> MediaInfo:
    return MediaInfo(
        path=path,
        container_format="mov,mp4,m4a,3gp,3g2,mj2",
        duration_seconds=duration,
        video=VideoStreamInfo(
            index=0,
            codec=codec,
            width=width,
            height=height,
            pixel_format="yuv422p10le",
            r_frame_rate=rate,
            avg_frame_rate=rate,
        ),
        audio=AudioStreamInfo(
            index=1, codec="pcm_s24le", sample_rate_hz=48000, channels=2
        )
        if audio
        else None,
    )


CTV_TARGET = TargetSpec(
    container="mp4",
    output_extension=".mp4",
    video_codec="h264",
    video_profile="high",
    pixel_format="yuv420p",
    width=1920,
    height=1080,
    frame_rate_behavior=FrameRateBehavior.PRESERVE_IF_ALLOWED,
    allowed_frame_rates=(
        Fraction(24000, 1001),
        Fraction(24),
        Fraction(25),
        Fraction(30000, 1001),
        Fraction(30),
    ),
    preferred_frame_rate=Fraction(30000, 1001),
    video_bitrate_mbps=20.0,
    audio=AudioTarget(loudness_lkfs=-24.0, true_peak_db=-2.0),
)


def make_job(info: MediaInfo | None = None, **kwargs) -> ExportJob:
    info = info or make_info()
    fields = {
        "source_path": info.path,
        "source_info": info,
        "output_path": Path("C:/out/source_CTV.mp4"),
        "target": CTV_TARGET,
    }
    fields.update(kwargs)
    return ExportJob(**fields)


def pair_after(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args else None


# --- exact time formatting -------------------------------------------------


def test_format_seconds_is_exact_for_900_frames_at_2997():
    # 900 / (30000/1001) = 30.03 exactly. A float round-trip would not be.
    duration = MediaTime(900, R2997).seconds
    assert format_seconds(duration) == "30.030000"


def test_format_rate_uses_the_rational():
    assert format_rate(Fraction(30000, 1001)) == "30000/1001"
    assert format_rate(Fraction(25)) == "25/1"


# --- trimming --------------------------------------------------------------


def test_trim_seeks_on_the_input_with_exact_duration():
    info = make_info(rate=R2997, codec="h264")
    trim = TrimRange(MediaTime(300, R2997), MediaTime(1200, R2997))
    args = build_export_args(make_job(info, trim=trim))

    assert "-accurate_seek" in args
    ss = args.index("-ss")
    assert args[ss - 1] == "-accurate_seek"
    # Input seeking: -ss must come before -i, or FFmpeg decodes and discards.
    assert ss < args.index("-i")
    assert args[ss + 1] == format_seconds(trim.in_point.seconds)
    # 900 frames at 29.97.
    assert pair_after(args, "-t") == "30.030000"
    assert args.index("-t") > args.index("-i")


def test_untrimmed_export_has_no_seek_arguments():
    args = build_export_args(make_job())
    assert "-ss" not in args
    assert "-t" not in args
    assert "-accurate_seek" not in args


def test_trim_from_zero_needs_no_seek_but_still_limits_duration():
    info = make_info(rate=R2997)
    trim = TrimRange(MediaTime(0, R2997), MediaTime(900, R2997))
    args = build_export_args(make_job(info, trim=trim))
    assert "-ss" not in args
    assert pair_after(args, "-t") == "30.030000"


# --- frame rate ------------------------------------------------------------


def test_allowed_source_rate_is_preserved_exactly():
    args = build_export_args(make_job(make_info(rate=R2997)))
    assert pair_after(args, "-r") == "30000/1001"
    assert pair_after(args, "-fps_mode") == "cfr"
    assert "29.97" not in args
    assert "30/1" not in args


def test_disallowed_source_rate_is_converted_to_the_preferred_rate():
    target = TargetSpec(
        **{**CTV_TARGET.__dict__, "allowed_frame_rates": (Fraction(30000, 1001),)}
    )
    args = build_export_args(make_job(make_info(rate=R25), target=target))
    assert pair_after(args, "-r") == "30000/1001"


def test_force_behaviour_overrides_an_allowed_source_rate():
    target = TargetSpec(
        **{**CTV_TARGET.__dict__, "frame_rate_behavior": FrameRateBehavior.FORCE}
    )
    args = build_export_args(make_job(make_info(rate=R25), target=target))
    assert pair_after(args, "-r") == "30000/1001"


def test_nearest_allowed_rate_when_no_preference_is_stated():
    target = TargetSpec(
        **{
            **CTV_TARGET.__dict__,
            "allowed_frame_rates": (Fraction(25), Fraction(50)),
            "preferred_frame_rate": None,
        }
    )
    assert resolve_frame_rate(make_info(rate=R2997), target) == Fraction(25)


@pytest.mark.parametrize(
    "info",
    [
        make_info(rate=R23976),
        make_info(rate=R25),
        make_info(rate=R2997),
        make_info(rate=R2997, audio=False),
        make_info(width=4096, height=2160),
    ],
)
def test_minterpolate_never_appears(info):
    args = build_export_args(make_job(info))
    joined = " ".join(args)
    assert "minterpolate" not in joined
    assert "fps=" not in joined


# --- geometry --------------------------------------------------------------


def test_different_aspect_scales_and_pads_but_never_crops():
    args = build_export_args(make_job(make_info(width=4096, height=2160)))
    chain = pair_after(args, "-vf")
    assert chain is not None
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in chain
    assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2" in chain
    assert "setsar=1" in chain
    assert "crop" not in chain
    # The whole chain is one argv element, not several.
    assert chain in args


def test_matching_resolution_emits_no_video_filter():
    args = build_export_args(make_job(make_info(width=1920, height=1080)))
    assert "-vf" not in args


# --- codecs and rate control ----------------------------------------------


def test_video_codec_and_bitrate_control():
    args = build_export_args(make_job())
    assert pair_after(args, "-c:v") == "libx264"
    assert pair_after(args, "-preset") == "medium"
    assert pair_after(args, "-profile:v") == "high"
    assert pair_after(args, "-pix_fmt") == "yuv420p"
    assert pair_after(args, "-b:v") == "20M"
    assert pair_after(args, "-maxrate") == "30M"
    assert pair_after(args, "-bufsize") == "60M"
    assert "-crf" not in args


def test_crf_when_the_target_states_no_bitrate():
    target = TargetSpec(**{**CTV_TARGET.__dict__, "video_bitrate_mbps": None})
    args = build_export_args(make_job(target=target))
    assert pair_after(args, "-crf") == "18"
    assert "-b:v" not in args


def test_audio_settings_come_from_the_target():
    args = build_export_args(make_job())
    assert pair_after(args, "-c:a") == "aac"
    assert pair_after(args, "-ac") == "2"
    assert pair_after(args, "-ar") == "48000"
    assert pair_after(args, "-b:a") == "320k"


def test_no_audio_source_is_silent_not_synthesised():
    args = build_export_args(make_job(make_info(audio=False)))
    assert "-an" in args
    assert "-c:a" not in args
    assert "-b:a" not in args
    assert "-af" not in args
    # No audio to map, either.
    assert "0:a:0?" not in args


def test_stream_mapping_takes_only_the_first_of_each():
    args = build_export_args(make_job())
    assert pair_after(args, "-map") == "0:v:0"
    assert args[args.index("0:v:0") + 2] == "0:a:0?"


# --- loudness --------------------------------------------------------------


def test_two_pass_loudnorm_carries_every_measurement():
    job = make_job(
        normalize_loudness=True,
        source_loudness=LoudnessResult(integrated_lufs=-19.83, raw=dict(MEASURED)),
    )
    chain = pair_after(build_export_args(job), "-af")
    assert chain is not None
    for fragment in (
        "I=-24",
        "TP=-2",
        "LRA=7",
        "measured_I=-19.83",
        "measured_TP=-4.12",
        "measured_LRA=6.7",
        "measured_thresh=-30.21",
        "offset=0.02",
        "linear=true",
        "print_format=summary",
    ):
        assert fragment in chain


def test_single_pass_fallback_without_measurements():
    chain = build_loudnorm_filter(AudioTarget(loudness_lkfs=-24.0), None)
    assert chain is not None
    assert "measured_" not in chain
    assert "linear=true" not in chain


def test_no_loudnorm_when_the_target_states_no_loudness():
    assert build_loudnorm_filter(AudioTarget(), LoudnessResult(-19.8, raw=MEASURED)) is None


def test_normalisation_is_opt_in():
    job = make_job(source_loudness=LoudnessResult(integrated_lufs=-19.83, raw=MEASURED))
    assert "-af" not in build_export_args(job)


# --- container and overwrite ----------------------------------------------


def test_faststart_for_mp4_only():
    assert pair_after(build_export_args(make_job()), "-movflags") == "+faststart"
    target = TargetSpec(**{**CTV_TARGET.__dict__, "container": "mxf", "faststart": True})
    assert "-movflags" not in build_export_args(make_job(target=target))


def test_overwrite_flag():
    assert "-n" in build_export_args(make_job())
    assert "-y" not in build_export_args(make_job())
    assert "-y" in build_export_args(make_job(overwrite=True))
    assert "-n" not in build_export_args(make_job(overwrite=True))


def test_always_batchable_and_machine_readable():
    args = build_export_args(make_job())
    assert "-hide_banner" in args
    assert "-nostdin" in args
    assert "-nostats" in args
    assert pair_after(args, "-progress") == "pipe:1"


# --- argv hygiene ----------------------------------------------------------


def test_every_element_is_a_string_and_output_is_last():
    job = make_job()
    args = build_export_args(job)
    assert all(isinstance(a, str) for a in args)
    assert args[-1] == str(job.output_path)


def test_paths_with_spaces_and_unicode_stay_single_elements():
    source = Path("C:/media/clip with spaces éç作品.mov")
    output = Path("C:/out folder/clip with spaces éç作品_CTV.mp4")
    info = make_info(path=source)
    args = build_export_args(make_job(info, source_path=source, output_path=output))
    assert args[args.index("-i") + 1] == str(source)
    assert args[-1] == str(output)
    # No quoting is added: the list is passed to Popen verbatim.
    assert '"' not in args[-1]


def test_command_text_quotes_only_for_display():
    args = build_export_args(make_job())
    text = build_command_text(args)
    assert text.startswith("ffmpeg ")
    assert "-hide_banner" in text
