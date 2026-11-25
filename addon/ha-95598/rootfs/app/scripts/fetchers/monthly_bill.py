import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from scripts.const import ELECTRIC_BILL_SUMMARY_URL
from scripts.fetchers.browser_api import BrowserApiError, BrowserBillApiClient
from scripts.fetchers.vue_state import normalize_bill_detail, selected_vue_data
from scripts.pages import bill_selectors as selectors
from scripts.support.data_rows import MonthlyBillRow
from scripts.support.monthly_billing import MonthlyBillingService


@dataclass
class MonthlyBillSyncResult:
    rows: list[dict[str, Any]]
    verified: bool
    failed_months: list[str]


class MonthlyBillFetcher:
    def __init__(
        self,
        *,
        db,
        driver_wait_time: int,
        log_page_state: Callable[[Any, str], None],
        step_sleep: Callable[[Any, str], None],
        api_client_factory=BrowserBillApiClient,
    ) -> None:
        self.db = db
        self.driver_wait_time = driver_wait_time
        self.log_page_state = log_page_state
        self.step_sleep = step_sleep
        self.api_client_factory = api_client_factory

    def sync(self, driver, user_id: str):
        if self.db is None:
            return MonthlyBillSyncResult([], False, [])
        if not self.db.connect_user_db(user_id):
            return MonthlyBillSyncResult([], False, [])
        rows = []
        failed_months = set()
        try:
            billing = MonthlyBillingService(self.db)
            try:
                verified = self._sync_api(driver, user_id, billing, rows)
            except Exception as exc:
                logging.warning(
                    "Browser bill API failed for %s; falling back to page navigation (%s).",
                    user_id[-4:],
                    type(exc).__name__,
                )
                verified = False

            if not verified:
                verified, failed_months = self._sync_navigation(driver, billing, rows)
        finally:
            self.db.close_connect()

        return MonthlyBillSyncResult(rows, verified, sorted(failed_months))

    def _sync_api(self, driver, user_id: str, billing, rows: list[dict]) -> bool:
        client = self.api_client_factory(driver, self.driver_wait_time)
        client.open()
        current_year = datetime.now().year
        current_month = datetime.now().month
        target_years = [current_year]
        if current_month <= 2:
            target_years.append(current_year - 1)

        verified = False
        for target_year in target_years:
            response = client.get_summary(target_year)
            entries, matched_user = self._summary_entries(response, user_id, target_year)
            if not matched_user:
                if target_year == current_year:
                    raise BrowserApiError("current_user_missing_from_bill_summary")
                continue
            verified = True
            for entry in entries:
                month_key = entry["month"]
                if not self._needs_sync(month_key):
                    continue
                detail_response = client.get_detail(entry["payload"])
                row = self._api_detail_row(detail_response, month_key, user_id)
                self._save_bill_row(billing, row, rows)
        return verified

    def _summary_entries(self, response: dict, user_id: str, target_year: int):
        data = response.get("data") if isinstance(response, dict) else None
        accounts = data.get("consList") if isinstance(data, dict) else None
        if not isinstance(accounts, list):
            raise BrowserApiError("bill_summary_missing_cons_list")

        entries = []
        matched_user = False
        for account in accounts:
            if not isinstance(account, dict):
                continue
            account_matches = self._matches_user(account, user_id)
            for bill in account.get("billList") or []:
                if not isinstance(bill, dict):
                    continue
                if not account_matches and not self._matches_user(bill, user_id):
                    continue
                matched_user = True
                month_key = self._normalize_bill_month(bill.get("ym"), target_year)
                if not month_key.startswith(f"{target_year:04d}-"):
                    continue
                month_items = [item for item in bill.get("monthList") or [] if isinstance(item, dict)]
                month_item = month_items[0] if month_items else {}
                cons_no = bill.get("consNo") or account.get("consNoDst") or account.get("consNo")
                province_code = account.get("proCode") or bill.get("proCode")
                account_id = account.get("userId") or bill.get("userId")
                if not cons_no or not province_code or not account_id:
                    raise BrowserApiError("bill_summary_missing_detail_parameters")
                entries.append(
                    {
                        "month": month_key,
                        "payload": {
                            "data": {
                                "acctId": account_id,
                                "channelCode": "0902",
                                "consNo": cons_no,
                                "funcCode": "WEB",
                                "provinceCode": province_code,
                                "ym": bill.get("ym"),
                                "calcId": month_item.get("calcId") or "",
                            },
                            "serviceCode": "BCP_000026",
                            "source": "WEB",
                            "target": province_code,
                        },
                    }
                )
        return entries, matched_user

    @staticmethod
    def _normalize_bill_month(value, reference_year: int) -> str:
        text = str(value or "").strip().replace("/", "-")
        if len(text) == 6 and text.isdigit():
            return f"{text[:4]}-{text[4:]}"
        return MonthlyBillingService.normalize_month_value(text, reference_year)

    @staticmethod
    def _matches_user(values: dict, user_id: str) -> bool:
        expected = str(user_id).strip()
        return any(str(values.get(key) or "").strip() == expected for key in ("consNo", "consNoDst", "userId"))

    def _api_detail_row(self, response: dict, expected_month: str, user_id: str):
        data = response.get("data") if isinstance(response, dict) else None
        detail_rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(detail_rows, list) or not detail_rows or not isinstance(detail_rows[0], dict):
            raise BrowserApiError("bill_detail_missing_list_row")
        detail = normalize_bill_detail([{"data": {"billList": [detail_rows[0]]}}])
        if detail.get("month") != expected_month:
            raise BrowserApiError("bill_detail_month_mismatch")
        if detail.get("user_id") and str(detail["user_id"]) != str(user_id):
            raise BrowserApiError("bill_detail_user_mismatch")
        return MonthlyBillRow(
            month=detail["month"],
            total_usage=detail.get("usage"),
            total_charge=detail.get("charge"),
            valley_usage=detail.get("valley_usage") or 0.0,
            flat_usage=detail.get("flat_usage") or 0.0,
            peak_usage=detail.get("peak_usage") or 0.0,
            tip_usage=detail.get("tip_usage") or 0.0,
        ).to_official_dict()

    def _save_bill_row(self, billing, row: dict, rows: list[dict]) -> None:
        month_key = billing.upsert_official_bill(row)
        billing.refresh_year(month_key[:4])
        rows.append(row)
        logging.info("Stored official monthly bill for %s.", month_key)

    def _sync_navigation(self, driver, billing, rows: list[dict]):
        failed_months = set()
        verified = False
        self.open_summary_page(driver)
        available_years = self.get_available_years(driver)
        current_year = datetime.now().year
        current_month = datetime.now().month
        target_years = [year for year in available_years if year == current_year]
        if current_month <= 2 and (current_year - 1) in available_years:
            target_years.append(current_year - 1)

        for target_year in target_years:
            if not self.select_year(driver, target_year):
                continue
            verified = True
            existing_year = self.db.get_period_row("yearly_usage", "year", str(target_year))
            current_month_key = f"{current_year:04d}-{current_month:02d}"
            needs_deep_sync = not self._row_has_nonzero_tou(existing_year) or self._has_calculated_closed_months(
                target_year,
                current_month_key,
            )
            visible_months = self.get_visible_month_keys(driver)
            pending_months = [month_key for month_key in visible_months if self._needs_sync(month_key)]
            if needs_deep_sync:
                self.expand_summary(driver)
                visible_months = self.get_visible_month_keys(driver)
                pending_months = [month_key for month_key in visible_months if self._needs_sync(month_key)]
            if not pending_months:
                logging.info("Monthly bill TOU is already complete for visible months in %s.", target_year)
                continue

            for bill_index, month_key in enumerate(visible_months):
                if month_key not in pending_months:
                    continue
                try:
                    if not self.open_detail_by_index(driver, bill_index):
                        failed_months.add(month_key)
                        continue
                    detail = self.parse_detail(driver)
                    if detail is None:
                        failed_months.add(month_key)
                    else:
                        self._save_bill_row(billing, detail, rows)
                except Exception as exc:
                    failed_months.add(month_key)
                    logging.warning("Monthly bill detail failed for %s (%s).", month_key, type(exc).__name__)
                    break

                driver.back()
                self.step_sleep(driver, f"after_return_bill_summary_{target_year}_{bill_index}")
                WebDriverWait(driver, self.driver_wait_time).until(
                    EC.visibility_of_element_located((By.XPATH, selectors.BILL_CARD))
                )
                if needs_deep_sync:
                    self.expand_summary(driver)

        return verified, failed_months

    def open_summary_page(self, driver) -> None:
        driver.get(ELECTRIC_BILL_SUMMARY_URL)
        self.log_page_state(driver, "after_open_bill_summary_url")
        self.step_sleep(driver, "after_open_bill_summary_url")
        WebDriverWait(driver, self.driver_wait_time).until(
            EC.visibility_of_element_located((By.XPATH, selectors.BILL_CARD))
        )

    def get_available_years(self, driver) -> list[int]:
        years = []
        year_nodes = WebDriverWait(driver, self.driver_wait_time).until(
            EC.visibility_of_all_elements_located(
                (By.XPATH, selectors.BILL_YEAR_OPTIONS)
            )
        )
        for option in year_nodes:
            try:
                years.append(int(option.text.strip().replace("年", "")))
            except (TypeError, ValueError):
                continue
        return sorted(set(years), reverse=True)

    def select_year(self, driver, target_year: int) -> bool:
        target_year = int(target_year)
        try:
            active_year = driver.find_element(
                By.XPATH,
                selectors.BILL_ACTIVE_YEAR,
            ).text.strip()
            if active_year == f"{target_year}年":
                return True

            option = WebDriverWait(driver, self.driver_wait_time).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        selectors.bill_year_option(target_year),
                    )
                )
            )
            driver.execute_script("arguments[0].click();", option)
            self.step_sleep(driver, f"after_select_bill_year_{target_year}")
            WebDriverWait(driver, self.driver_wait_time).until(
                lambda d: d.find_element(
                    By.XPATH,
                    selectors.BILL_ACTIVE_YEAR,
                ).text.strip()
                == f"{target_year}年"
            )
            WebDriverWait(driver, self.driver_wait_time).until(
                EC.visibility_of_element_located((By.XPATH, selectors.BILL_CARD))
            )
            return True
        except Exception as exc:
            logging.warning("Failed to switch bill year to %s: %s", target_year, exc)
            return False

    def expand_summary(self, driver) -> None:
        for _ in range(6):
            buttons = driver.find_elements(By.XPATH, selectors.BILL_EXPAND_BUTTON)
            if not buttons:
                return
            previous_count = len(driver.find_elements(By.XPATH, selectors.BILL_CARD))
            driver.execute_script("arguments[0].click();", buttons[0])
            self.step_sleep(driver, "after_expand_bill_summary")
            current_count = len(driver.find_elements(By.XPATH, selectors.BILL_CARD))
            if current_count <= previous_count:
                return

    def get_visible_month_keys(self, driver) -> list[str]:
        month_keys = []
        month_nodes = driver.find_elements(By.XPATH, selectors.BILL_MONTH_LABEL)
        for node in month_nodes:
            month_key = self.parse_month_key(node.text)
            if month_key:
                month_keys.append(month_key)
        return month_keys

    def open_detail_by_index(self, driver, bill_index: int) -> bool:
        month_rows = driver.find_elements(By.XPATH, selectors.BILL_LIST_ROW)
        if bill_index >= len(month_rows):
            return False
        arrow = month_rows[bill_index].find_element(By.XPATH, selectors.BILL_ROW_DETAIL_ARROW)
        driver.execute_script("arguments[0].click();", arrow)
        self.step_sleep(driver, f"after_open_bill_detail_{bill_index}")
        WebDriverWait(driver, self.driver_wait_time).until(
            EC.visibility_of_element_located((By.XPATH, selectors.BILL_DETAIL_CYCLE))
        )
        return True

    def parse_detail(self, driver):
        try:
            detail = normalize_bill_detail(selected_vue_data(driver))
            if detail.get("month"):
                return MonthlyBillRow(
                    month=detail.get("month"),
                    total_usage=detail.get("usage"),
                    total_charge=detail.get("charge"),
                    valley_usage=detail.get("valley_usage") or 0.0,
                    flat_usage=detail.get("flat_usage") or 0.0,
                    peak_usage=detail.get("peak_usage") or 0.0,
                    tip_usage=detail.get("tip_usage") or 0.0,
                ).to_official_dict()
        except Exception as exc:
            logging.debug("Failed to parse monthly bill detail from Vue state, fallback to DOM: %s", exc)

        try:
            cycle_text = driver.find_element(By.XPATH, selectors.BILL_DETAIL_CYCLE).text
            month_key = self.parse_month_key(cycle_text)
            if month_key is None:
                return None

            total_usage = self._read_total_usage(driver)
            tou_values = self._read_tou_values(driver)
            if total_usage is None:
                total_usage = round(sum(tou_values.values()), 2)

            return MonthlyBillRow(
                month=month_key,
                total_usage=total_usage,
                total_charge=self._read_total_charge(driver),
                **tou_values,
            ).to_official_dict()
        except Exception as exc:
            logging.warning("Failed to parse monthly bill detail: %s", exc)
            return None

    def parse_month_key(self, bill_time_text: str) -> str | None:
        match = re.search(r"(\d{4})/(\d{2})/\d{2}", str(bill_time_text).strip())
        if not match:
            return None
        return f"{match.group(1)}-{match.group(2)}"

    def _needs_sync(self, month_key: str) -> bool:
        return not self.db.is_official_monthly_bill(month_key)

    def _has_calculated_closed_months(self, target_year: int, current_month_key: str) -> bool:
        return any(
            row.get("source") == self.db.MONTHLY_SOURCE_CALCULATED
            and str(row.get("month", "")).startswith(f"{target_year:04d}-")
            and str(row.get("month", "")) < current_month_key
            for row in self.db.get_recent_monthly_history(months=24)
        )

    def _row_has_nonzero_tou(self, row) -> bool:
        if not row:
            return False
        return any(float(row.get(field, 0.0) or 0.0) > 0 for field in ("valley_usage", "flat_usage", "peak_usage", "tip_usage"))

    def _read_total_usage(self, driver):
        try:
            total_usage_text = driver.find_element(
                By.XPATH,
                selectors.BILL_TOTAL_USAGE,
            ).text
            return float(total_usage_text.strip())
        except Exception:
            return None

    def _read_tou_values(self, driver) -> dict[str, float]:
        tou_values = {
            "valley_usage": 0.0,
            "flat_usage": 0.0,
            "peak_usage": 0.0,
            "tip_usage": 0.0,
        }
        tou_items = driver.find_elements(
            By.XPATH,
            selectors.BILL_TOU_ITEMS,
        )
        for item in tou_items:
            label = item.find_element(By.XPATH, selectors.BILL_TOU_LABEL).text.strip()
            value_text = item.find_element(By.XPATH, selectors.BILL_TOU_VALUE).text.strip()
            value = float(value_text or 0)
            if "低谷" in label or "谷" in label:
                tou_values["valley_usage"] = value
            elif "平" in label:
                tou_values["flat_usage"] = value
            elif "峰" in label:
                tou_values["peak_usage"] = value
            elif "尖" in label:
                tou_values["tip_usage"] = value
        return tou_values

    def _read_total_charge(self, driver):
        total_charge = None
        matched_charge_count = 0
        charge_items = driver.find_elements(
            By.XPATH,
            selectors.BILL_CHARGE_ITEMS,
        )
        for item in charge_items:
            spans = item.find_elements(By.XPATH, "./span")
            if len(spans) < 4:
                continue
            label = spans[0].text.strip()
            amount_text = spans[3].text.strip()
            amount = float(amount_text or 0)
            if "峰" in label or "平" in label or "谷" in label or "尖" in label:
                total_charge = (total_charge or 0.0) + amount
                matched_charge_count += 1
        if matched_charge_count:
            return round(total_charge or 0.0, 2)
        return None
