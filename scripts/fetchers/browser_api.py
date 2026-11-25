import logging
from typing import Any

from selenium.webdriver.support.wait import WebDriverWait

from scripts.const import ELECTRIC_BILL_SUMMARY_URL


class BrowserApiError(RuntimeError):
    pass


class BrowserBillApiClient:
    def __init__(self, driver, driver_wait_time: int) -> None:
        self.driver = driver
        self.driver_wait_time = driver_wait_time

    def open(self) -> None:
        self.driver.get(ELECTRIC_BILL_SUMMARY_URL)
        WebDriverWait(self.driver, self.driver_wait_time).until(
            lambda current_driver: current_driver.execute_script(
                """
                return Array.from(document.querySelectorAll('*')).some((el) => {
                  const vm = el.__vue__;
                  return vm && vm.$tools && typeof vm.$tools.apiPost === 'function'
                    && vm.$api && vm.$api.queryMultiHouseholdMultiMonthElectricityData
                    && vm.$api.queryDetailedMonthlyElectricityData;
                });
                """
            )
        )

    def get_summary(self, year: int) -> dict[str, Any]:
        return self._post(
            "queryMultiHouseholdMultiMonthElectricityData",
            {
                "target": "",
                "source": "",
                "serviceCode": "",
                "data": {"year": str(year)},
            },
        )

    def get_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("queryDetailedMonthlyElectricityData", payload)

    def _post(self, api_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.driver.execute_async_script(
            """
            const apiName = arguments[0];
            const payload = arguments[1];
            const done = arguments[arguments.length - 1];
            const vm = Array.from(document.querySelectorAll('*'))
              .map((el) => el.__vue__)
              .find((item) => item && item.$tools && typeof item.$tools.apiPost === 'function'
                && item.$api && item.$api[apiName]);
            if (!vm) {
              done({ok: false, error: 'browser_api_not_found'});
              return;
            }
            Promise.resolve(vm.$tools.apiPost(payload, vm.$api[apiName]))
              .then((response) => done({ok: true, response}))
              .catch((error) => done({
                ok: false,
                error: String(error && error.message ? error.message : error).slice(0, 200)
              }));
            """,
            api_name,
            payload,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            error = result.get("error") if isinstance(result, dict) else "invalid_browser_response"
            raise BrowserApiError(str(error or "browser_api_failed"))
        response = result.get("response")
        if not isinstance(response, dict):
            raise BrowserApiError("browser_api_returned_non_object")
        logging.debug("Browser bill API completed: %s", api_name)
        return response
