"""Web automation client for FatSecret using Patchright.

Allows creating custom foods without a Premier API subscription by interacting with
the FatSecret web interface.
"""
from __future__ import annotations

import os
import pathlib
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from patchright.sync_api import Browser, BrowserContext, Page, sync_playwright


class FatSecretWebError(RuntimeError):
    """Errors during FatSecret web automation."""


class FatSecretWebClient:
    """Singleton/Persistent Web Automation Client with 5-minute idle auto-close."""

    def __init__(self, session_path: pathlib.Path | None = None) -> None:
        self.session_path = (
            session_path
            or pathlib.Path.home() / ".config" / "fatsecret-mcp" / "web_session.json"
        )
        self._lock = threading.Lock()
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._timer: threading.Timer | None = None

    def _reset_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(300.0, self.close)
        self._timer.daemon = True
        self._timer.start()

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._context is not None:
                try:
                    self._context.close()
                except Exception:
                    pass
                self._context = None
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    def _ensure_browser(self) -> tuple[BrowserContext, Page]:
        with self._lock:
            if self._playwright is None:
                self._playwright = sync_playwright().start()
            if self._browser is None:
                self._browser = self._playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-gpu"],
                )
            if self._context is None:
                self.session_path.parent.mkdir(parents=True, exist_ok=True)
                kwargs: dict[str, Any] = {"viewport": {"width": 1368, "height": 768}}
                if self.session_path.exists():
                    kwargs["storage_state"] = str(self.session_path)
                self._context = self._browser.new_context(**kwargs)

            page = self._context.new_page()
            self._reset_timer()
            return self._context, page

    def _login_if_needed(self, page: Page) -> None:
        email = os.environ.get("FATSECRET_WEB_EMAIL", "").strip()
        password = os.environ.get("FATSECRET_WEB_PASSWORD", "").strip()

        if not email or not password:
            raise FatSecretWebError(
                "FATSECRET_WEB_EMAIL and FATSECRET_WEB_PASSWORD must be configured "
                "in environment variables or .env to use web custom food creation."
            )

        page.goto("https://foods.fatsecret.com/Auth.aspx?pa=s", wait_until="domcontentloaded")
        if "Default.aspx" in page.url or "pa=m" in page.url or "Diary.aspx" in page.url:
            return

        pwd_field = page.locator('input[id*="Logincontrol1_Password"]')
        if pwd_field.is_visible():
            page.fill('input[id*="Logincontrol1_Name"]', email)
            pwd_field.fill(password)
            page.check('input[id*="Logincontrol1_CreatePersistentCookie"]')
            pwd_field.press("Enter")
            page.wait_for_load_state("domcontentloaded")
            time.sleep(1)

            if self._context:
                self._context.storage_state(path=str(self.session_path))

    def create_custom_food(
        self,
        name: str,
        calories: float,
        protein: float = 0.0,
        fat: float = 0.0,
        carbs: float = 0.0,
        serving_size: str = "100 g",
        brand: str = "",
    ) -> dict[str, Any]:
        context, page = self._ensure_browser()
        try:
            page.goto("https://foods.fatsecret.com/Diary.aspx?pa=fjcr", wait_until="domcontentloaded")
            if "Auth.aspx" in page.url:
                self._login_if_needed(page)
                page.goto("https://foods.fatsecret.com/Diary.aspx?pa=fjcr", wait_until="domcontentloaded")

            if brand.strip():
                page.check('input[name="manufacturerType"][value="1"]')
                page.fill('input[name="manufacturerName"]', brand.strip())
            else:
                page.check('input[name="manufacturerType"][value="0"]')

            page.fill('input[name="title"]', name.strip())
            page.fill('input[name="servingSize"]', serving_size)
            page.fill('input[name="servingAmount"]', "100")
            page.select_option('select[name="servingAmountUnit"]', "g")

            page.fill('input[name="energyPerPortion"]', str(int(calories)))
            page.fill('input[name="proteinPerPortion"]', str(round(protein, 2)))
            page.fill('input[name="fatPerPortion"]', str(round(fat, 2)))
            page.fill('input[name="carbohydratePerPortion"]', str(round(carbs, 2)))

            page.check('input[name="sharing"][value="2"]')

            save_btn = page.locator('*:has-text("Save")').last
            save_btn.click()
            page.wait_for_load_state("domcontentloaded")
            time.sleep(1)

            parsed = parse_qs(urlparse(page.url).query)
            rid_list = parsed.get("rid", [])
            food_id = rid_list[0] if rid_list else None

            return {
                "success": True,
                "food_id": food_id,
                "food_name": name,
                "calories": calories,
                "protein": protein,
                "fat": fat,
                "carbs": carbs,
                "url": page.url,
            }
        finally:
            page.close()
            self._reset_timer()


web_client = FatSecretWebClient()
