"""Fail-fast checks before a capture: UI serves the renderer, Chromium launches.

Synthetic only: an httpx MockTransport stands in for the UI and a fake Playwright for
Chromium, so no browser, frontend or network is needed.
"""

import asyncio
import logging
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")
pytest.importorskip("playwright")

import httpx  # noqa: E402
from playwright.async_api import Error as PlaywrightError  # noqa: E402
from test_navigation import (  # noqa: E402
    SyntheticCapture,
    SyntheticRenderer,
    follow,
    initial,
    service,
)

from agent_access.errors import AccessError  # noqa: E402
from agent_access.renderer import RENDERER_MARKER, FrozenRenderer  # noqa: E402

UI_BASE = "http://127.0.0.1:5173"
RENDERER_ENTRY = Path(__file__).resolve().parents[3] / "frontend" / "mcp-render.html"
# The SPA's own index: what a frontend built before the renderer answers for any path.
OTHER_PAGE = "<!doctype html><html><head><title>Alpha Deck</title></head><body></body></html>"
NOT_INSTALLED = PlaywrightError(
    "BrowserType.launch: Executable doesn't exist at /synthetic/browsers/headless_shell\n"
    "Looks like Playwright was just installed or updated."
)
OTHER_LAUNCH_FAILURE = PlaywrightError("BrowserType.launch: synthetic launch failure " + "x" * 400)


def renderer_page():
    return RENDERER_ENTRY.read_text("utf-8")


class FakeBrowser:
    def __init__(self, chromium):
        self.chromium = chromium

    async def new_context(self, **_):
        raise PlaywrightError("synthetic captured content that must never be logged")

    async def close(self):
        self.chromium.closed += 1


class FakeChromium:
    def __init__(self, failure=None):
        self.failure = failure
        self.launches = 0
        self.closed = 0

    async def launch(self, *, headless):
        assert headless is True
        self.launches += 1
        if self.failure is not None:
            raise self.failure
        return FakeBrowser(self)


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class UI:
    """A synthetic frontend; `mode` can change between reads."""

    def __init__(self, mode="renderer"):
        self.mode = mode
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.mode == "unreachable":
            raise httpx.ConnectError("synthetic refusal", request=request)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if self.mode == "server_error":
            return httpx.Response(502, text="bad gateway")
        if self.mode == "not_found":
            return httpx.Response(404, text="missing")
        if self.mode == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:5173/elsewhere"})
        if self.mode == "other_page":
            return httpx.Response(200, text=OTHER_PAGE)
        assert self.mode == "renderer"
        return httpx.Response(200, text=renderer_page())


class CheckedRenderer(FrozenRenderer):
    """The real preflight; the page render itself is synthetic."""

    async def render(self, envelope, snapshot_id, settings, target_id=None):
        return await SyntheticRenderer().render(envelope, snapshot_id, settings, target_id)


def checked(ui=None, failure=None, renderer_class=CheckedRenderer):
    ui = ui or UI()
    chromium = FakeChromium(failure)
    renderer = renderer_class(
        UI_BASE,
        transport=httpx.MockTransport(ui),
        playwright_factory=lambda: FakePlaywright(chromium),
    )
    return renderer, ui, chromium


def test_marker_is_the_renderer_entry_title():
    assert RENDERER_MARKER in renderer_page()
    assert RENDERER_MARKER not in OTHER_PAGE


@pytest.mark.parametrize(
    "mode,code,retryable,status",
    [
        ("unreachable", "ui_unavailable", True, None),
        ("timeout", "ui_unavailable", True, None),
        ("server_error", "ui_unavailable", True, 502),
        ("not_found", "renderer_not_served", False, 404),
        ("redirect", "renderer_not_served", False, 302),
        ("other_page", "renderer_not_served", False, None),
    ],
)
def test_ui_check_maps_each_failure_and_skips_the_browser(mode, code, retryable, status):
    renderer, ui, chromium = checked(UI(mode))
    with pytest.raises(AccessError) as error:
        asyncio.run(renderer.preflight())
    assert (error.value.code, error.value.retryable) == (code, retryable)
    assert UI_BASE in error.value.message
    if status is not None:
        assert f"(HTTP {status} for /mcp-render.html)" in error.value.message
    else:
        assert "HTTP" not in error.value.message
    assert len(ui.requests) == 1  # No redirect was followed.
    assert ui.requests[0].method == "GET"
    assert str(ui.requests[0].url) == UI_BASE + "/mcp-render.html"
    assert chromium.launches == 0


def test_ui_check_accepts_the_renderer_and_runs_on_every_call():
    renderer, ui, chromium = checked()
    asyncio.run(renderer.preflight())
    asyncio.run(renderer.preflight())
    assert len(ui.requests) == 2
    assert (chromium.launches, chromium.closed) == (1, 1)
    ui.mode = "other_page"  # The UI is re-checked even after the browser is known good.
    with pytest.raises(AccessError) as error:
        asyncio.run(renderer.preflight())
    assert error.value.code == "renderer_not_served"


@pytest.mark.parametrize(
    "failure,code,retryable",
    [
        (NOT_INSTALLED, "browser_not_installed", False),
        (OTHER_LAUNCH_FAILURE, "browser_unavailable", True),
    ],
)
def test_browser_check_maps_launch_failures_and_logs_only_the_first_line(
    failure, code, retryable, caplog
):
    caplog.set_level(logging.WARNING, logger="agent_access.renderer")
    renderer, _, chromium = checked(failure=failure)
    with pytest.raises(AccessError) as error:
        asyncio.run(renderer.preflight())
    assert (error.value.code, error.value.retryable) == (code, retryable)
    if code == "browser_not_installed":
        assert "python -m playwright install --only-shell chromium" in error.value.message
    assert chromium.launches == 1
    (record,) = caplog.records
    logged = record.getMessage().split(" message=", 1)[1]
    assert logged == str(failure).splitlines()[0][:200]
    assert "Looks like" not in record.getMessage()


def test_browser_success_is_cached_and_failure_is_not():
    renderer, _, chromium = checked(failure=NOT_INSTALLED)
    for _ in range(2):
        with pytest.raises(AccessError) as error:
            asyncio.run(renderer.preflight())
        assert error.value.code == "browser_not_installed"
    assert chromium.launches == 2  # A failure is re-checked on the next read.
    chromium.failure = None  # Chromium installed; no restart.
    asyncio.run(renderer.preflight())
    asyncio.run(renderer.preflight())
    assert chromium.launches == 3  # Success is remembered.


@pytest.mark.parametrize(
    "failure,code,retryable",
    [
        (NOT_INSTALLED, "browser_not_installed", False),
        (OTHER_LAUNCH_FAILURE, "browser_unavailable", True),
    ],
)
def test_render_launch_uses_the_same_mapping_and_forgets_a_cached_success(failure, code, retryable):
    renderer, _, chromium = checked(renderer_class=FrozenRenderer)
    asyncio.run(renderer.preflight())
    chromium.failure = failure
    envelope = {"responses": [], "context": {"clock_response": {}}}
    with pytest.raises(AccessError) as error:
        asyncio.run(renderer.render(envelope, "synthetic", {}))
    assert (error.value.code, error.value.retryable) == (code, retryable)
    with pytest.raises(AccessError):
        asyncio.run(renderer.preflight())  # The failed launch is re-checked, not trusted.
    assert chromium.launches == 3


def test_render_errors_after_launch_keep_class_only_logging(caplog):
    caplog.set_level(logging.WARNING, logger="agent_access.renderer")
    renderer, _, chromium = checked(renderer_class=FrozenRenderer)
    envelope = {"responses": [], "context": {"clock_response": {}}}
    with pytest.raises(AccessError) as error:
        asyncio.run(renderer.render(envelope, "synthetic", {}))
    assert (error.value.code, error.value.retryable) == ("browser_unavailable", True)
    assert chromium.closed == 1
    assert [record.getMessage() for record in caplog.records] == [
        "browser_error exception_class=Error"
    ]


FAILURES = {  # code -> (the broken renderer's setup, retryable)
    "browser_not_installed": (lambda: checked(failure=NOT_INSTALLED), False),
    "browser_unavailable": (lambda: checked(failure=OTHER_LAUNCH_FAILURE), True),
    "ui_unavailable": (lambda: checked(UI("unreachable")), True),
    "renderer_not_served": (lambda: checked(UI("other_page")), False),
}


@pytest.mark.parametrize("code", sorted(FAILURES))
def test_failed_preflight_never_starts_the_initial_capture(code):
    broken, retryable = FAILURES[code]

    async def run():
        svc = service(renderer=broken()[0])
        result, _ = await svc.call("read_dashboard", {"dashboard": "board"})
        assert not result["ok"]
        assert result["error"]["code"] == code
        assert result["error"]["retryable"] is retryable
        assert svc.capture.calls == []
        assert not svc.store._items

    asyncio.run(run())


@pytest.mark.parametrize("code", sorted(FAILURES))
def test_failed_preflight_never_starts_the_followed_capture(code):
    broken, retryable = FAILURES[code]

    async def run():
        svc = service(renderer=checked()[0])
        parent = await initial(svc)
        svc.renderer = broken()[0]
        result, _ = await svc.call("read_dashboard", follow(parent))
        assert result["error"]["code"] == code
        assert result["error"]["retryable"] is retryable
        assert len(svc.capture.calls) == 1  # Only the parent was captured.
        assert list(svc.store._items) == [parent]

    asyncio.run(run())


def test_two_reads_launch_the_browser_once_and_a_failed_read_rechecks():
    async def run():
        renderer, ui, chromium = checked(failure=OTHER_LAUNCH_FAILURE)
        svc = service(renderer=renderer, capture=SyntheticCapture())
        failed, _ = await svc.call("read_dashboard", {"dashboard": "board"})
        assert failed["error"]["code"] == "browser_unavailable"
        chromium.failure = None
        for _ in range(2):
            result, _ = await svc.call("read_dashboard", {"dashboard": "board"})
            assert result["ok"]
        assert chromium.launches == 2  # One failed check, then one cached success.
        assert len(ui.requests) == 3 and len(svc.capture.calls) == 2

    asyncio.run(run())
