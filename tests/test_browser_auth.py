import asyncio
import threading

from zhenyun_script_platform_mcp.client.browser_auth import BrowserAuthenticator
from zhenyun_script_platform_mcp.config import Settings


def test_browser_login_moves_sync_playwright_out_of_running_asyncio_loop(tmp_path, monkeypatch):
    authenticator = BrowserAuthenticator(
        Settings(
            base_url="https://gateway.dev.example.com",
            chrome_profile_dir=tmp_path / "profile",
        )
    )
    caller_thread = threading.current_thread().name

    def fake_login_sync(**_kwargs):
        return {"thread": threading.current_thread().name}

    monkeypatch.setattr(authenticator, "_login_sync", fake_login_sync)

    async def call_login():
        return authenticator.login(username="user", password="password")

    result = asyncio.run(call_login())
    assert result["thread"].startswith("script-platform-sso")
    assert result["thread"] != caller_thread
