"""The shipped profiles load, and malformed ones are rejected loudly.

Profiles are contributed by people, so the interesting assertions here are the
rejections: an error that does not name the file and the offending key is an
error the contributor cannot act on.
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from framecheck.app.models.profile import CheckStatus, Severity
from framecheck.app.profiles.loader import ProfileLoader, default_specs_dir
from framecheck.app.profiles.schema import ProfileSchemaError, derive_label, parse_profile

SPECS_DIR = default_specs_dir()
SPEC_FILES = sorted(SPECS_DIR.glob("*.json"))

EXPECTED_IDS = {
    "streaming_ctv",
    "youtube",
    "online_video",
    "meta_feed",
    "meta_reels",
    "meta_instream",
    "snapchat",
    "reddit",
    "x",
}


def _minimal(**overrides) -> dict:
    data = {
        "id": "example",
        "name": "Example",
        "requirements": {"container": {"equals": "mp4"}},
    }
    data.update(overrides)
    return data


def test_specs_directory_is_shipped() -> None:
    assert SPEC_FILES, f"no profile JSON found in {SPECS_DIR}"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.stem)
def test_shipped_profile_round_trips(path: Path) -> None:
    profile = parse_profile(json.loads(path.read_text(encoding="utf-8")), path)
    assert profile.id == path.stem
    assert profile.name
    assert profile.rules, "a profile with no requirements checks nothing"
    assert profile.source_path == path


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.stem)
def test_shipped_profile_has_a_manual_check(path: Path) -> None:
    profile = parse_profile(json.loads(path.read_text(encoding="utf-8")), path)
    manual = [r for r in profile.rules if r.manual]
    assert manual, "every destination needs at least one human check"
    assert all(r.severity is Severity.MANUAL and not r.fixable for r in manual)


def test_shipped_ids_are_the_expected_nine_and_unique() -> None:
    loader = ProfileLoader()
    profiles = loader.load_all()
    assert loader.errors == []
    ids = [p.id for p in profiles]
    assert len(ids) == len(set(ids))
    assert set(ids) == EXPECTED_IDS


def test_profiles_are_sorted_by_name() -> None:
    names = [p.name for p in ProfileLoader().load_all()]
    assert names == sorted(names, key=str.lower)


def test_frame_rates_are_exact_rationals() -> None:
    profile = ProfileLoader().get("streaming_ctv")
    rule = profile.rule_for("video.frame_rate")
    values = [rate.value for rate in rule.allowed]
    assert Fraction(30000, 1001) in values
    assert Fraction(24000, 1001) in values
    assert all(isinstance(v, Fraction) for v in values)
    assert profile.target.preferred_frame_rate == Fraction(30000, 1001)
    assert Fraction(30000, 1001) in profile.target.allowed_frame_rates


def test_review_keys_become_manual_rules() -> None:
    profile = parse_profile(
        _minimal(requirements={"review.disclaimer": {"severity": "fail", "fixable": True}})
    )
    rule = profile.rules[0]
    assert rule.manual is True
    # severity and fixable in the JSON cannot override what manual means
    assert rule.severity is Severity.MANUAL
    assert rule.fixable is False


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("video.codec", "Video codec"),
        ("audio.sample_rate_hz", "Audio sample rate"),
        ("video.bitrate_mbps", "Video bitrate"),
        ("audio.loudness_lkfs", "Audio loudness"),
        ("file.size_mb", "File size"),
        ("video.frame_rate_mode", "Video frame rate mode"),
    ],
)
def test_labels_are_derived_from_the_key(field: str, expected: str) -> None:
    assert derive_label(field) == expected


def test_explicit_label_wins() -> None:
    profile = parse_profile(
        _minimal(requirements={"review.disclaimer": {"manual": True, "label": "Legal disclaimer"}})
    )
    assert profile.rules[0].label == "Legal disclaimer"


# --------------------------------------------------------------------------
# Rejections


def test_unknown_field_key_is_rejected_by_name() -> None:
    with pytest.raises(ProfileSchemaError) as exc:
        parse_profile(
            _minimal(requirements={"video.colour_science": {"equals": "rec709"}}),
            Path("bad.json"),
        )
    message = str(exc.value)
    assert "bad.json" in message
    assert "video.colour_science" in message


def test_unknown_rule_key_is_rejected() -> None:
    with pytest.raises(ProfileSchemaError, match="maximum_mbps"):
        parse_profile(_minimal(requirements={"video.codec": {"equals": "h264", "maximum_mbps": 4}}))


def test_bad_severity_is_rejected_with_the_legal_values() -> None:
    with pytest.raises(ProfileSchemaError) as exc:
        parse_profile(
            _minimal(requirements={"container": {"equals": "mp4", "severity": "critical"}})
        )
    message = str(exc.value)
    assert "critical" in message
    assert "warning" in message  # the message lists what is legal


def test_missing_id_is_rejected() -> None:
    with pytest.raises(ProfileSchemaError, match="'id'"):
        parse_profile({"name": "No id"})


def test_non_slug_id_is_rejected() -> None:
    with pytest.raises(ProfileSchemaError, match="slug"):
        parse_profile(_minimal(id="Streaming CTV"))


def test_missing_name_is_rejected() -> None:
    with pytest.raises(ProfileSchemaError, match="'name'"):
        parse_profile({"id": "example"})


def test_malformed_json_is_reported_by_the_loader(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{ not json at all", encoding="utf-8")
    loader = ProfileLoader(tmp_path)
    assert loader.load_all() == []
    assert any("broken.json" in error for error in loader.errors)


@pytest.mark.parametrize(
    "requirement",
    [
        {"equals": "mp4", "allowed": ["mp4", "mov"]},
        {"equals": 1920, "min": 1280},
        {"allowed": [1080, 720], "max": 2160},
        {"tolerance": 2},
        {"min": 30, "max": 10},
        {},
    ],
)
def test_nonsense_comparisons_are_rejected(requirement: dict) -> None:
    with pytest.raises(ProfileSchemaError):
        parse_profile(_minimal(requirements={"video.width": requirement}))


def test_bad_target_types_are_rejected() -> None:
    with pytest.raises(ProfileSchemaError, match="width"):
        parse_profile(_minimal(target={"width": "1920"}))


def test_unknown_target_key_is_rejected() -> None:
    with pytest.raises(ProfileSchemaError, match="video_bitrate"):
        parse_profile(_minimal(target={"video_bitrate": 20}))


def test_bad_frame_rate_behavior_is_rejected() -> None:
    with pytest.raises(ProfileSchemaError, match="frame_rate_behavior"):
        parse_profile(_minimal(target={"frame_rate_behavior": "always_60"}))


def test_unknown_top_level_key_is_rejected() -> None:
    with pytest.raises(ProfileSchemaError, match="requirments"):
        parse_profile(_minimal(requirments={}))


# --------------------------------------------------------------------------
# Loader


def _write(path: Path, **overrides) -> None:
    path.write_text(json.dumps(_minimal(**overrides)), encoding="utf-8")


def test_loader_tolerates_one_broken_file(tmp_path: Path) -> None:
    _write(tmp_path / "good_one.json", id="good_one", name="Good One")
    _write(tmp_path / "good_two.json", id="good_two", name="Good Two")
    (tmp_path / "broken.json").write_text('{"id": "broken"}', encoding="utf-8")

    loader = ProfileLoader(tmp_path)
    ids = [p.id for p in loader.load_all()]

    assert ids == ["good_one", "good_two"]
    assert len(loader.errors) == 1
    assert "broken.json" in loader.errors[0]
    assert loader.get("good_two") is not None
    assert loader.get("broken") is None


def test_loader_reports_duplicate_ids(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", id="same", name="A")
    _write(tmp_path / "b.json", id="same", name="B")
    loader = ProfileLoader(tmp_path)
    assert len(loader.load_all()) == 1
    assert any("duplicate" in error for error in loader.errors)


def test_loader_reports_a_missing_directory(tmp_path: Path) -> None:
    loader = ProfileLoader(tmp_path / "nope")
    assert loader.load_all() == []
    assert loader.errors and "not found" in loader.errors[0]


def test_reload_picks_up_a_new_file(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", id="a", name="A")
    loader = ProfileLoader(tmp_path)
    assert len(loader.load_all()) == 1
    _write(tmp_path / "b.json", id="b", name="B")
    assert len(loader.load_all()) == 1  # cached
    assert len(loader.reload()) == 2


def test_default_loader_points_at_the_repo_specs_dir() -> None:
    assert ProfileLoader().specs_dir == SPECS_DIR
    assert (SPECS_DIR / "SCHEMA.md").is_file()


def test_manual_status_is_not_a_pass() -> None:
    # Guard the contract the rest of the suite leans on.
    assert CheckStatus.MANUAL_REVIEW.rank > CheckStatus.PASS.rank


def test_a_user_spec_replaces_the_built_in_with_the_same_id(tmp_path: Path) -> None:
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    shipped.mkdir(), user.mkdir()
    _write(shipped / "ctv.json", id="ctv", name="CTV")
    _write(shipped / "yt.json", id="yt", name="YouTube")
    _write(user / "my_ctv.json", id="ctv", name="CTV (ours)")
    _write(user / "extra.json", id="extra", name="Extra")

    loader = ProfileLoader(shipped, user)
    assert [p.name for p in loader.load_all()] == ["CTV (ours)", "Extra", "YouTube"]
    assert loader.errors == []


def test_a_missing_user_folder_is_not_an_error(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", id="a", name="A")
    loader = ProfileLoader(tmp_path, tmp_path / "never-made")
    assert len(loader.load_all()) == 1
    assert loader.errors == []


def test_the_default_loader_reads_the_user_folder(isolated_app_data: Path) -> None:
    user = isolated_app_data / "Framecheck" / "specs"
    user.mkdir(parents=True)
    _write(user / "mine.json", id="mine_only", name="Mine")
    assert ProfileLoader().get("mine_only") is not None
