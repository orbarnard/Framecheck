"""Batch model: counts, rollups and the progress arithmetic behind the bar.

Pure logic. Results are built by hand -- nothing here runs FFmpeg, and nothing
here imports Qt.
"""

from __future__ import annotations

from pathlib import Path

from framecheck.app.models.batch import BatchItem, BatchItemState, BatchPlan
from framecheck.app.models.export_job import ExportResult, ExportState
from framecheck.app.models.media_file import MediaFile
from framecheck.app.models.profile import CheckStatus
from framecheck.app.models.validation_result import CheckResult, ValidationReport


def make_item(name: str, state: BatchItemState = BatchItemState.PENDING) -> BatchItem:
    return BatchItem(MediaFile(Path(f"C:/media/{name}.mov")), state=state)


def make_report(*statuses: CheckStatus, profile_id: str = "streaming_ctv") -> ValidationReport:
    checks = tuple(
        CheckResult(label=f"Check {n}", status=s, actual="--", expected="--")
        for n, s in enumerate(statuses)
    )
    return ValidationReport(profile_id=profile_id, profile_name="Streaming / CTV", checks=checks)


def make_result(*reports: ValidationReport, state: str = ExportState.DONE) -> ExportResult:
    # No rollup under test reads `job`, and building a real ExportJob would drag
    # in a probed MediaInfo for no gain, so it is left unset.
    return ExportResult(job=None, state=state, output_path=Path("C:/out/a.mp4"), reports=reports)


# -- counts and rollups ----------------------------------------------------


def test_counts_across_a_mixed_plan() -> None:
    plan = BatchPlan(
        items=[
            make_item("a", BatchItemState.DONE),
            make_item("b", BatchItemState.DONE),
            make_item("c", BatchItemState.FAILED),
            make_item("d", BatchItemState.SKIPPED),
            make_item("e", BatchItemState.RUNNING),
            make_item("f", BatchItemState.PENDING),
        ]
    )

    assert plan.total == 6
    assert plan.completed == 2
    assert plan.failed == 1
    # SKIPPED has settled; RUNNING and PENDING have not.
    assert plan.remaining == 2
    assert plan.is_finished is False


def test_an_empty_plan_is_not_finished() -> None:
    plan = BatchPlan()

    assert plan.total == 0
    assert plan.remaining == 0
    assert plan.overall_progress == 0.0
    # Nothing ran, so nothing completed. The completion block must not appear.
    assert plan.is_finished is False


def test_is_finished_only_once_nothing_is_moving() -> None:
    plan = BatchPlan(items=[make_item("a", BatchItemState.DONE), make_item("b")])
    assert plan.is_finished is False

    plan.items[1].state = BatchItemState.RUNNING
    assert plan.is_finished is False

    plan.items[1].state = BatchItemState.CANCELLED
    assert plan.is_finished is True


# -- progress --------------------------------------------------------------


def test_overall_progress_counts_the_file_mid_encode() -> None:
    plan = BatchPlan(items=[make_item("a", BatchItemState.RUNNING), make_item("b")])
    plan.items[0].progress = 0.5

    assert plan.overall_progress == 0.25


def test_a_finished_item_counts_whole_whatever_its_progress_says() -> None:
    plan = BatchPlan(items=[make_item("a", BatchItemState.DONE), make_item("b")])
    plan.items[0].progress = 0.3

    assert plan.overall_progress == 0.5


def test_overall_progress_reaches_exactly_one_when_all_settle() -> None:
    plan = BatchPlan(
        items=[
            make_item("a", BatchItemState.DONE),
            make_item("b", BatchItemState.FAILED),
            make_item("c", BatchItemState.SKIPPED),
        ]
    )

    assert plan.overall_progress == 1.0


def test_out_of_range_item_progress_is_clamped() -> None:
    plan = BatchPlan(items=[make_item("a", BatchItemState.RUNNING)])
    plan.items[0].progress = 4.0
    assert plan.overall_progress == 1.0

    plan.items[0].progress = -2.0
    assert plan.overall_progress == 0.0


# -- lookup ----------------------------------------------------------------


def test_item_for_finds_by_media_file_key() -> None:
    item = make_item("clip")
    plan = BatchPlan(items=[make_item("other"), item])

    assert plan.item_for(item.media_file.key) is item


def test_item_for_misses_return_none() -> None:
    plan = BatchPlan(items=[make_item("a")])

    assert plan.item_for("c:/nowhere/absent.mov") is None


# -- item status -----------------------------------------------------------


def test_status_is_none_before_verification() -> None:
    item = make_item("a")
    assert item.status is None

    # An export that produced no reports has not been verified either.
    item.state = BatchItemState.DONE
    item.result = make_result()
    assert item.status is None


def test_status_is_the_worst_across_every_report() -> None:
    item = make_item("a", BatchItemState.DONE)
    item.result = make_result(
        make_report(CheckStatus.PASS, CheckStatus.PASS),
        make_report(CheckStatus.PASS, CheckStatus.WARNING, profile_id="youtube"),
    )

    assert item.status is CheckStatus.WARNING


def test_a_single_failure_outranks_a_pile_of_passes() -> None:
    item = make_item("a", BatchItemState.DONE)
    item.result = make_result(make_report(CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.WARNING))

    assert item.status is CheckStatus.FAIL


def test_manual_review_outranks_pass() -> None:
    item = make_item("a", BatchItemState.DONE)
    item.result = make_result(make_report(CheckStatus.PASS, CheckStatus.MANUAL_REVIEW))

    assert item.status is CheckStatus.MANUAL_REVIEW


def test_status_counts_ignore_unverified_items() -> None:
    verified = make_item("a", BatchItemState.DONE)
    verified.result = make_result(make_report(CheckStatus.PASS))
    plan = BatchPlan(items=[verified, make_item("b", BatchItemState.FAILED)])

    assert plan.status_counts() == {CheckStatus.PASS: 1}


# -- summaries -------------------------------------------------------------


def test_summary_for_each_state() -> None:
    assert make_item("a").summary() == "Queued"
    assert make_item("a", BatchItemState.SKIPPED).summary() == "Skipped"
    assert make_item("a", BatchItemState.CANCELLED).summary() == "Cancelled"

    running = make_item("a", BatchItemState.RUNNING)
    assert running.summary() == "Exporting"
    running.progress = 0.42
    assert running.summary() == "Exporting 42%"

    failed = make_item("a", BatchItemState.FAILED)
    assert failed.summary() == "Export failed"
    failed.error = "ffmpeg exited with code 1"
    assert failed.summary() == "ffmpeg exited with code 1"

    done = make_item("a", BatchItemState.DONE)
    assert done.summary() == "Exported"
    done.result = make_result(make_report(CheckStatus.PASS))
    assert done.summary() == "All checks pass"
    done.result = make_result(make_report(CheckStatus.PASS, CheckStatus.FAIL))
    assert done.summary() == "1 fail"


# -- reset -----------------------------------------------------------------


def test_reset_returns_every_item_to_pending_and_clears_outcomes() -> None:
    done = make_item("a", BatchItemState.DONE)
    done.result = make_result(make_report(CheckStatus.PASS))
    done.progress = 1.0
    failed = make_item("b", BatchItemState.FAILED)
    failed.error = "unreadable source"
    failed.progress = 0.7
    plan = BatchPlan(items=[done, failed])

    plan.reset()

    assert [i.state for i in plan.items] == [BatchItemState.PENDING] * 2
    assert all(i.result is None and i.error is None and i.progress == 0.0 for i in plan.items)
    assert plan.completed == 0
    assert plan.failed == 0
    assert plan.remaining == 2
    assert plan.overall_progress == 0.0
    assert plan.status_counts() == {}
