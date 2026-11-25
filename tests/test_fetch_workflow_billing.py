from types import SimpleNamespace

import pytest

from scripts.fetchers.monthly_bill import MonthlyBillSyncResult
from scripts.support.fetch_progress import FETCH_STAGE_ORDER
from scripts.support.fetch_workflow import FetchWorkflow


class Updater:
    def __init__(self):
        self.progress = {"stage": "persist", "fetch_date": "2026-11-25"}
        self.stages = []

    def get_progress(self, _user_id):
        return self.progress

    def get_cached_user_data(self, _user_id):
        return {}

    def save_partial_data(self, _user_id, **_fields):
        return None

    def update_progress(self, _user_id, **fields):
        self.progress.update(fields)

    def update_progress_stage(self, _user_id, stage, fetch_date=None):
        self.progress["stage"] = stage
        self.progress["fetch_date"] = fetch_date
        self.stages.append(stage)


class Fetcher:
    def __init__(self, result):
        self.result = result
        self.db = object()
        self.db_type = "sqlite"
        self.usage_page = SimpleNamespace(open_for_user=lambda *_args: None)

    def _progress_date(self):
        return "2026-11-25"

    def _is_progress_current(self, _progress):
        return True

    def _has_completed_stage(self, progress, stage):
        current = (progress or {}).get("stage", "none")
        return FETCH_STAGE_ORDER[current] >= FETCH_STAGE_ORDER[stage]

    def _get_daily_usage_data(self, _driver):
        return [], []

    def _save_user_data(self, *_args):
        return None

    def _backfill_missing_daily_history(self, *_args):
        return 0

    def _sync_monthly_bill_tou(self, *_args):
        return self.result


def run_billing_stage(result):
    updater = Updater()
    workflow = FetchWorkflow(Fetcher(result))
    workflow.run(object(), "1234567890123", 0, updater)
    return updater


def test_failed_month_is_saved_and_never_marks_complete():
    result = MonthlyBillSyncResult([], True, ["2026-10"])

    with pytest.raises(RuntimeError, match="2026-10"):
        run_billing_stage(result)


def test_failed_month_progress_is_persisted_before_error():
    result = MonthlyBillSyncResult([], True, ["2026-10"])
    updater = Updater()
    workflow = FetchWorkflow(Fetcher(result))

    with pytest.raises(RuntimeError):
        workflow.run(object(), "1234567890123", 0, updater)

    assert updater.progress["billing_failed_months"] == ["2026-10"]
    assert updater.progress["stage"] == "persist"
    assert "billing" not in updater.stages
    assert "complete" not in updater.stages


def test_verified_full_sync_marks_billing_and_complete():
    updater = run_billing_stage(MonthlyBillSyncResult([], True, []))

    assert updater.progress["billing_failed_months"] == []
    assert updater.stages[-2:] == ["billing", "complete"]
