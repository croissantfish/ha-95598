import pytest

from scripts.fetchers.browser_api import BrowserApiError
from scripts.fetchers.monthly_bill import MonthlyBillFetcher
from scripts.support.monthly_billing import MonthlyBillingService
from scripts.support.db import SqliteDB


USER_ID = "1234567890123"


class BillDb:
    MONTHLY_SOURCE_CALCULATED = SqliteDB.MONTHLY_SOURCE_CALCULATED
    MONTHLY_SOURCE_OFFICIAL = SqliteDB.MONTHLY_SOURCE_OFFICIAL

    def __init__(self, official_months=()):
        self.official_months = set(official_months)
        self.upserts = []
        self.refreshed_years = []
        self.closed = False

    def connect_user_db(self, user_id):
        return True

    def close_connect(self):
        self.closed = True

    def get_recent_monthly_history(self, months):
        return []

    def get_period_row(self, table, key, value):
        if table == "yearly_usage":
            return {"valley_usage": 1.0, "flat_usage": 1.0, "peak_usage": 1.0, "tip_usage": 0.0}
        return None

    def is_official_monthly_bill(self, month):
        return month in self.official_months

    def upsert_official_monthly_bill(self, row):
        self.upserts.append(row["month"])
        self.official_months.add(row["month"])
        return True

    def refresh_year_from_months(self, year):
        self.refreshed_years.append(year)
        return True


def summary_response(months):
    return {
        "data": {
            "consList": [
                {
                    "consNoDst": USER_ID,
                    "userId": "account-id",
                    "proCode": "province-code",
                    "billList": [
                        {
                            "consNo": USER_ID,
                            "ym": month.replace("-", ""),
                            "monthList": [{"calcId": f"calc-{month}"}],
                        }
                        for month in months
                    ],
                }
            ]
        }
    }


def detail_response(month):
    return {
        "data": {
            "list": [
                {
                    "ym": month.replace("-", ""),
                    "basicInfo": {"consNo": USER_ID, "monthPq": "12.5", "monthAmt": "6.25"},
                    "pvQtyList": [{"valQty": "2", "flatQty": "3", "peakQty": "4", "sharpQty": "3.5"}],
                    "prcGroupList": [],
                }
            ]
        }
    }


class FakeApiClient:
    def __init__(self, db, fail_month=None):
        self.db = db
        self.fail_month = fail_month
        self.opened = False
        self.detail_months = []

    def open(self):
        self.opened = True

    def get_summary(self, year):
        return summary_response(["2026-10", "2026-11"])

    def get_detail(self, payload):
        month = payload["data"]["ym"]
        month = f"{month[:4]}-{month[4:]}"
        if month == self.fail_month:
            raise BrowserApiError("detail_failed")
        if month == "2026-11":
            assert self.db.upserts == ["2026-10"]
        self.detail_months.append(month)
        return detail_response(month)


def make_fetcher(db, client):
    return MonthlyBillFetcher(
        db=db,
        driver_wait_time=1,
        log_page_state=lambda *_args: None,
        step_sleep=lambda *_args: None,
        api_client_factory=lambda *_args: client,
    )


def test_api_sync_persists_each_month_before_next_detail():
    db = BillDb()
    client = FakeApiClient(db)
    fetcher = make_fetcher(db, client)

    result = fetcher.sync(object(), USER_ID)

    assert client.opened is True
    assert client.detail_months == ["2026-10", "2026-11"]
    assert db.upserts == ["2026-10", "2026-11"]
    assert db.refreshed_years == ["2026", "2026"]
    assert [row["month"] for row in result.rows] == ["2026-10", "2026-11"]
    assert result.verified is True
    assert result.failed_months == []
    assert db.closed is True


def test_api_failure_falls_back_without_repeating_saved_months():
    db = BillDb()
    client = FakeApiClient(db, fail_month="2026-11")
    fetcher = make_fetcher(db, client)
    navigation_calls = []

    def navigate(driver, billing, rows):
        navigation_calls.append([row["month"] for row in rows])
        assert db.is_official_monthly_bill("2026-10") is True
        assert db.is_official_monthly_bill("2026-11") is False
        return True, set()

    fetcher._sync_navigation = navigate

    result = fetcher.sync(object(), USER_ID)

    assert navigation_calls == [["2026-10"]]
    assert result.verified is True
    assert result.failed_months == []


def test_navigation_saves_successful_month_and_records_failed_month(monkeypatch):
    db = BillDb()
    fetcher = make_fetcher(db, FakeApiClient(db))
    fetcher.open_summary_page = lambda driver: None
    fetcher.get_available_years = lambda driver: [2026]
    fetcher.select_year = lambda driver, year: True
    fetcher.get_visible_month_keys = lambda driver: ["2026-10", "2026-11", "2026-12"]
    fetcher.open_detail_by_index = lambda driver, index: True
    details = iter([detail_response("2026-10")["data"]["list"][0], TimeoutError()])

    def parse_detail(driver):
        value = next(details)
        if isinstance(value, Exception):
            raise value
        month = value["ym"]
        return {
            "month": f"{month[:4]}-{month[4:]}",
            "total_usage": 12.5,
            "total_charge": 6.25,
            "valley_usage": 2.0,
            "flat_usage": 3.0,
            "peak_usage": 4.0,
            "tip_usage": 3.5,
            "source": "official",
        }

    fetcher.parse_detail = parse_detail

    class Driver:
        def back(self):
            return None

    class Wait:
        def __init__(self, *_args):
            pass

        def until(self, _condition):
            return True

    monkeypatch.setattr("scripts.fetchers.monthly_bill.WebDriverWait", Wait)
    rows = []

    verified, failed_months = fetcher._sync_navigation(Driver(), MonthlyBillingService(db), rows)

    assert verified is True
    assert failed_months == {"2026-11"}
    assert db.upserts == ["2026-10"]
    assert [row["month"] for row in rows] == ["2026-10"]


def test_detail_month_mismatch_is_rejected():
    db = BillDb()
    fetcher = make_fetcher(db, FakeApiClient(db))

    with pytest.raises(BrowserApiError, match="month_mismatch"):
        fetcher._api_detail_row(detail_response("2026-10"), "2026-11", USER_ID)
