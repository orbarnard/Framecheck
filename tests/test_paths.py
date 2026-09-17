"""Output path rules. The safety property here is 'never overwrite the source'."""

from __future__ import annotations

from pathlib import Path

import pytest

from framecheck.app.utils.paths import (
    OutputDestination,
    OutputMode,
    build_output_path,
    format_size,
    sanitize_filename,
    same_file,
    shorten_path,
    unique_output_path,
)


# --------------------------------------------------------------------------
# sanitize_filename
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain name", "plain name"),
        ("a<b>c:d", "a_b_c_d"),
        ('quote"slash/back\\pipe|star*quest?', "quote_slash_back_pipe_star_quest_"),
        ("tab\tand\nnewline", "tab_and_newline"),
        ("null\x00byte", "null_byte"),
    ],
)
def test_illegal_characters_are_replaced(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("name", ["CON", "NUL", "COM1", "con", "Aux", "LPT9", "prn"])
def test_reserved_device_names_are_prefixed(name: str) -> None:
    result = sanitize_filename(name)
    assert result == f"_{name}"
    assert result.upper().lstrip("_") == name.upper()


@pytest.mark.parametrize("name", ["CONSOLE", "COMMS", "NULLABLE", "COM10"])
def test_names_merely_starting_with_a_reserved_word_are_left_alone(name: str) -> None:
    assert sanitize_filename(name) == name


@pytest.mark.parametrize("raw", ["", "   ", "...", ".", "<<<>>>"])
def test_empty_or_fully_stripped_names_fall_back(raw: str) -> None:
    assert sanitize_filename(raw, fallback="output") != ""
    assert sanitize_filename("", fallback="mything") == "mything"


@pytest.mark.parametrize("raw", ["name...", "name.", "  name  ", "name. ", " name..."])
def test_trailing_dots_and_surrounding_space_are_stripped(raw: str) -> None:
    assert sanitize_filename(raw) == "name"


def test_length_is_capped() -> None:
    assert len(sanitize_filename("a" * 500)) == 200
    assert len(sanitize_filename("a" * 199)) == 199


# --------------------------------------------------------------------------
# OutputDestination
# --------------------------------------------------------------------------


def test_same_as_source_resolves_to_the_sources_parent(tmp_path: Path) -> None:
    source = tmp_path / "sub" / "clip.mp4"
    destination = OutputDestination.same_as_source()
    assert destination.mode is OutputMode.SAME_AS_SOURCE
    assert destination.resolve_dir(source) == source.parent


def test_same_as_source_with_no_source_resolves_to_nothing() -> None:
    assert OutputDestination.same_as_source().resolve_dir(None) is None


def test_custom_destination_ignores_the_source(tmp_path: Path, tmp_output_dir: Path) -> None:
    destination = OutputDestination.custom(tmp_output_dir)
    assert destination.mode is OutputMode.CUSTOM
    assert destination.resolve_dir(tmp_path / "elsewhere" / "clip.mp4") == tmp_output_dir
    assert destination.resolve_dir(None) == tmp_output_dir


def test_custom_destination_accepts_a_string(tmp_output_dir: Path) -> None:
    assert OutputDestination.custom(str(tmp_output_dir)).custom_dir == tmp_output_dir


def test_display_text_flags_the_same_as_source_mode(tmp_path: Path, tmp_output_dir: Path) -> None:
    source = tmp_path / "clip.mp4"
    same = OutputDestination.same_as_source().display_text(source)
    assert "same as source" in same.lower()
    assert str(tmp_path) in same

    custom = OutputDestination.custom(tmp_output_dir).display_text(source)
    assert custom == str(tmp_output_dir)
    assert "same as source" not in custom.lower()


def test_display_text_without_a_source_still_reads_sensibly() -> None:
    assert OutputDestination.same_as_source().display_text(None) == "Same folder as source"


# --------------------------------------------------------------------------
# build_output_path
# --------------------------------------------------------------------------


def test_default_shape_is_prefix_tag_then_stem(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    result = build_output_path(source, OutputDestination.same_as_source(), suffix_tag="CTV")
    assert result == tmp_path / "FC_CTV-clip.mp4"


def test_exports_are_stamped_with_the_framecheck_prefix(tmp_path: Path) -> None:
    """Conformed files must be identifiable beside the originals they came from."""
    result = build_output_path(tmp_path / "spot.mov", OutputDestination.same_as_source())
    assert result.name.startswith("FC_")


def test_prefix_is_not_doubled_when_reconforming_an_export(tmp_path: Path) -> None:
    """Re-cutting an export for a second destination must not give FC_FC_."""
    source = tmp_path / "FC_CTV-spot.mp4"
    result = build_output_path(source, OutputDestination.same_as_source(), suffix_tag="YT")
    # The destination tag is replaced, not stacked.
    assert result.name == "FC_YT-spot.mp4"
    assert "FC_FC_" not in result.name


def test_user_supplied_filename_is_left_exactly_as_typed(tmp_path: Path) -> None:
    """An edited filename is the user's decision; do not stamp it."""
    result = build_output_path(
        tmp_path / "clip.mov", OutputDestination.same_as_source(), filename="delivery final.mp4"
    )
    assert result.name == "delivery final.mp4"


def test_output_lands_in_the_custom_directory(tmp_path: Path, tmp_output_dir: Path) -> None:
    source = tmp_path / "clip.mov"
    result = build_output_path(source, OutputDestination.custom(tmp_output_dir), suffix_tag="CTV")
    assert result == tmp_output_dir / "FC_CTV-clip.mp4"


def test_explicit_filename_overrides_the_tag(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    result = build_output_path(
        source, OutputDestination.same_as_source(), suffix_tag="CTV", filename="my delivery.mkv"
    )
    assert result == tmp_path / "my delivery.mkv"


def test_explicit_filename_is_sanitized(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    result = build_output_path(
        source, OutputDestination.same_as_source(), filename="bad:name?.mp4"
    )
    assert result.name == "bad_name_.mp4"


@pytest.mark.parametrize("extension", [".mov", "mov"])
def test_extension_is_normalised_with_or_without_a_leading_dot(
    tmp_path: Path, extension: str
) -> None:
    source = tmp_path / "clip.mp4"
    result = build_output_path(
        source, OutputDestination.same_as_source(), suffix_tag="CTV", extension=extension
    )
    assert result == tmp_path / "FC_CTV-clip.mov"


def test_filename_without_an_extension_takes_the_default(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    result = build_output_path(
        source, OutputDestination.same_as_source(), filename="delivery", extension=".mov"
    )
    assert result == tmp_path / "delivery.mov"


def test_output_is_never_the_source_file_itself(tmp_path: Path) -> None:
    """Whatever the naming rules produce, an export must not target its own input."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"pretend this is a movie")
    result = build_output_path(
        source, OutputDestination.same_as_source(), filename="clip.mp4"
    )
    assert not same_file(result, source)
    assert result != source
    assert result == tmp_path / "clip_framecheck.mp4"


def test_tagged_output_next_to_an_already_tagged_source_differs(tmp_path: Path) -> None:
    source = tmp_path / "clip_CTV.mp4"
    source.write_bytes(b"pretend this is a movie")
    result = build_output_path(source, OutputDestination.same_as_source(), suffix_tag="CTV")
    assert result != source
    assert not same_file(result, source)


# --------------------------------------------------------------------------
# unique_output_path
# --------------------------------------------------------------------------


def test_unique_output_path_passes_a_free_name_through(tmp_output_dir: Path) -> None:
    target = tmp_output_dir / "clip.mp4"
    assert unique_output_path(target) == target


def test_unique_output_path_appends_a_counter(tmp_output_dir: Path) -> None:
    target = tmp_output_dir / "clip.mp4"
    target.write_bytes(b"taken")
    assert unique_output_path(target) == tmp_output_dir / "clip (2).mp4"

    (tmp_output_dir / "clip (2).mp4").write_bytes(b"also taken")
    assert unique_output_path(target) == tmp_output_dir / "clip (3).mp4"


# --------------------------------------------------------------------------
# same_file
# --------------------------------------------------------------------------


def test_same_file_sees_through_a_dot_dot_spelling(tmp_path: Path) -> None:
    real = tmp_path / "clip.mp4"
    real.write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    alias = tmp_path / "sub" / ".." / "clip.mp4"

    assert alias != real
    assert same_file(real, alias)
    assert same_file(alias, real)


def test_same_file_is_false_for_different_files(tmp_path: Path) -> None:
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    assert not same_file(a, b)


def test_same_file_handles_paths_that_do_not_exist(tmp_path: Path) -> None:
    ghost = tmp_path / "ghost.mp4"
    assert same_file(ghost, tmp_path / "sub" / ".." / "ghost.mp4")
    assert not same_file(ghost, tmp_path / "other.mp4")


# --------------------------------------------------------------------------
# Display helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [
        (None, "--"),
        (0, "--"),
        (512, "512 B"),
        (1023, "1023 B"),
        (1024, "1 KB"),
        (1_048_576, "1.0 MB"),
        (1_572_864, "1.5 MB"),
        (5 * 1024**3, "5.0 GB"),
        (5000 * 1024**3, "5000.0 GB"),
    ],
)
def test_format_size(num_bytes: int | None, expected: str) -> None:
    assert format_size(num_bytes) == expected


def test_shorten_path_leaves_short_paths_alone() -> None:
    short = r"C:\media\clip.mp4"
    assert shorten_path(short) == short


@pytest.mark.parametrize("max_length", [20, 40, 64, 80])
def test_shorten_path_stays_within_the_limit(max_length: int) -> None:
    long_path = "C:\\" + "\\".join("directory" for _ in range(20)) + "\\clip.mp4"
    result = shorten_path(long_path, max_length)
    assert len(result) <= max_length
    assert "..." in result
    assert result.startswith("C:\\")
    assert result.endswith("clip.mp4")


def test_shorten_path_accepts_a_path_object() -> None:
    path = Path(r"C:\media\clip.mp4")
    assert shorten_path(path) == str(path)
