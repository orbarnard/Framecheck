"""Grouping destinations into the fewest output files.

The assertion that matters most is the negative one: when the shapes differ,
the report must say "two masters", not "we will crop it".
"""

from __future__ import annotations

from framecheck.app.profiles.compatibility import analyze
from framecheck.app.profiles.loader import ProfileLoader

LOADER = ProfileLoader()


def ids(group) -> set[str]:
    return {p.id for p in group.profiles}


def test_no_profiles_is_not_an_error() -> None:
    report = analyze([])
    assert report.output_count == 0
    assert report.reason


def test_landscape_destinations_share_one_master() -> None:
    profiles = [LOADER.get(i) for i in ("streaming_ctv", "youtube", "online_video")]
    report = analyze(profiles)

    assert report.single_master_possible is True
    assert report.output_count == 1
    assert ids(report.groups[0]) == {"streaming_ctv", "youtube", "online_video"}
    assert "1920x1080" in report.reason
    assert report.conflicts == ()


def test_incompatible_shapes_need_separate_masters() -> None:
    profiles = [LOADER.get(i) for i in ("streaming_ctv", "meta_reels", "snapchat")]
    report = analyze(profiles)

    assert report.single_master_possible is False
    assert report.output_count == 2

    labels = {g.label for g in report.groups}
    assert labels == {"Landscape master", "Vertical master"}

    by_label = {g.label: ids(g) for g in report.groups}
    assert by_label["Landscape master"] == {"streaming_ctv"}
    assert by_label["Vertical master"] == {"meta_reels", "snapchat"}

    assert report.conflicts
    assert any("16:9" in c and "9:16" in c for c in report.conflicts)


def test_a_split_never_proposes_cropping() -> None:
    report = analyze([LOADER.get("streaming_ctv"), LOADER.get("meta_reels")])
    text = " ".join([report.reason, *report.conflicts, *(g.reason for g in report.groups)]).lower()

    assert "crop" not in text
    assert "creative decision" in text


def test_groups_with_the_same_shape_class_are_still_distinguishable() -> None:
    # 4:5 and 9:16 are both vertical; two groups named "Vertical master" would
    # be useless in a picker.
    report = analyze([LOADER.get("meta_feed"), LOADER.get("meta_reels"), LOADER.get("x")])
    labels = [g.label for g in report.groups]

    assert len(labels) == len(set(labels)) == 3
    assert "Vertical master (4:5)" in labels
    assert "Vertical master (9:16)" in labels
    assert "Square master" in labels


def test_largest_common_resolution_is_taken() -> None:
    # Snapchat's target is 1080x1920 and Reels' is the same; X is square 1200.
    report = analyze([LOADER.get("meta_reels"), LOADER.get("snapchat")])
    assert report.output_count == 1
    assert "1080x1920" in report.groups[0].reason


def test_a_single_profile_is_its_own_group() -> None:
    report = analyze([LOADER.get("meta_feed")])
    assert report.single_master_possible is True
    assert report.output_count == 1
    assert report.groups[0].label == "Vertical master"
    assert report.groups[0].primary.id == "meta_feed"


def test_a_shape_free_profile_rides_along_with_whatever_master_exists() -> None:
    vertical = analyze([LOADER.get("meta_reels"), LOADER.get("youtube")])
    assert vertical.output_count == 1
    assert ids(vertical.groups[0]) == {"meta_reels", "youtube"}


def test_shape_free_profiles_alone_make_one_master() -> None:
    report = analyze([LOADER.get("youtube")])
    assert report.output_count == 1
    assert report.single_master_possible is True


def test_every_shipped_profile_lands_in_exactly_one_group() -> None:
    profiles = LOADER.load_all()
    report = analyze(profiles)
    grouped = [p.id for g in report.groups for p in g.profiles]

    assert sorted(grouped) == sorted(p.id for p in profiles)
    assert len(grouped) == len(set(grouped))
    assert report.single_master_possible is False
