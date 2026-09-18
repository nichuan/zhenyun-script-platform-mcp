"""Browser SSO fallback used only when the current access token can no longer be reused."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import httpx

from ..config import Settings
from ..exceptions import AuthenticationError


class BrowserAuthenticator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _authorization(self, token: str) -> str:
        if self._settings.authorization_scheme == "raw":
            return token
        return f"Bearer {token}"

    def _validate(self, token: str) -> None:
        try:
            response = httpx.get(
                f"{self._settings.base_url}/sada/v1/marmot-admin/is-admin",
                headers={"Authorization": self._authorization(token)},
                timeout=self._settings.timeout,
                verify=self._settings.verify_ssl,
            )
        except httpx.HTTPError as exc:
            raise AuthenticationError(
                f"Token validation failed after browser login: {type(exc).__name__}",
                retryable=True,
            ) from exc
        text = response.text
        content_type = response.headers.get("content-type", "")
        if (
            response.status_code != 200
            or "ACCESS_TOKEN_" in text.upper()
            or "json" not in content_type.lower()
        ):
            raise AuthenticationError(
                "Browser login produced a token that the Script Platform rejected"
            )

    @staticmethod
    def _first_selector(page: Any, selectors: tuple[str, ...]) -> str | None:
        for selector in selectors:
            if page.locator(selector).count() > 0:
                return selector
        return None

    def login(self, *, username: str, password: str | None) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise AuthenticationError(
                "Automatic browser login requires the project Playwright dependency"
            ) from exc

        self._settings.chrome_profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        query = urlencode(
            {
                "client_id": self._settings.client_id,
                "response_type": "code",
                "nonce": "platform",
                "redirect_uri": self._settings.redirect_uri,
            }
        )
        auth_url = f"{self._settings.authorize_url}?{query}"

        with sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(self._settings.chrome_profile_dir),
                    executable_path=str(self._settings.chrome_path),
                    headless=self._settings.auth_headless,
                    args=[
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
            except Exception as exc:
                raise AuthenticationError(
                    f"Unable to start Chrome for automatic SSO: {type(exc).__name__}"
                ) from exc

            try:
                page = context.pages[0] if context.pages else context.new_page()
                callback: dict[str, str] = {}

                def capture_callback(request: Any) -> None:
                    url = request.url
                    if "/oauth/login/bykey" not in url:
                        return
                    from urllib.parse import parse_qs, urlparse

                    values = parse_qs(urlparse(url).query)
                    if values.get("error"):
                        callback["error"] = values["error"][0]
                    elif values.get("code"):
                        callback["code"] = "received"

                page.on("request", capture_callback)
                page.goto(auth_url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1_000)

                user_selector = self._first_selector(
                    page, ("input[name=username]", "#username", "input[id=username]")
                )
                password_selector = self._first_selector(
                    page, ("input[name=password]", "#password", "input[id=password]")
                )
                if user_selector and password_selector:
                    if not username or not password:
                        raise AuthenticationError(
                            "SSO requires credentials. Configure SCRIPT_PLATFORM_SSO_USERNAME and "
                            "store the password in protected local storage with "
                            "zhenyun-script-platform-auth store-password."
                        )
                    page.fill(user_selector, username)
                    page.fill(password_selector, password)
                    submit_selector = self._first_selector(
                        page,
                        (
                            "#kc-login",
                            "input[name=login]",
                            "button[type=submit]",
                            "input[type=submit]",
                        ),
                    )
                    if submit_selector:
                        page.click(submit_selector)
                    else:
                        page.press(password_selector, "Enter")

                    page.wait_for_timeout(2_500)
                    if "sso.going-link.com" in page.url:
                        error_selector = self._first_selector(
                            page,
                            (
                                "#input-error",
                                ".kc-feedback-text",
                                ".alert-error",
                                ".pf-c-alert__title",
                            ),
                        )
                        error_text = ""
                        if error_selector:
                            error_text = page.locator(error_selector).first.inner_text().strip()
                        if error_text:
                            raise AuthenticationError(
                                f"SSO login did not complete: {error_text[:200]}"
                            )

                deadline = time.monotonic() + 75
                token = ""
                while time.monotonic() < deadline:
                    if callback.get("error"):
                        raise AuthenticationError(
                            f"SSO callback rejected the login: {callback['error']}"
                        )
                    token = page.evaluate(
                        "() => { try { return String(sessionStorage.getItem('access_token') || ''); "
                        "} catch (_) { return ''; } }"
                    )
                    if isinstance(token, str) and len(token) > 20:
                        break
                    page.wait_for_timeout(500)
                if not token:
                    host = page.evaluate("() => location.host")
                    title = page.title()
                    callback_status = "received" if callback.get("code") else "not observed"
                    message = (
                        "Automatic SSO completed without an access token; "
                        f"callback={callback_status}, final_host={host}, title={title[:80]}"
                    )
                    raise AuthenticationError(message)

                self._validate(token)
                return {"access_token": token}
            finally:
                context.close()


__all__ = ["BrowserAuthenticator"]
