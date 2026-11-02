"""Render real dashboard DOM against one frozen capture, with no live API fallback."""

import hashlib
import json
import logging
from urllib.parse import urlsplit

from .capture import loopback_base, request_key, response_key
from .errors import AccessError

VIEWPORT = {"width": 1440, "height": 1080}
MAX_PNG_BYTES = 8 * 1024 * 1024


class FrozenRenderer:
    def __init__(self, ui_base: str, *, timeout_ms: int = 40000):
        self.ui_base = loopback_base(ui_base)
        self.timeout_ms = timeout_ms

    async def render(
        self, envelope: dict, snapshot_id: str, settings: dict, target_id: str | None = None
    ) -> tuple[dict, bytes, dict]:
        from playwright.async_api import Error, TimeoutError, async_playwright

        frozen = {
            response_key(item["method"], item["path"], item["query"]): item
            for item in envelope["responses"]
        }
        origin = urlsplit(self.ui_base)
        violations = []
        asset_hashes = {}
        asset_bytes = 0

        async def route_request(route):
            nonlocal asset_bytes
            request = route.request
            parsed = urlsplit(request.url)
            if request.method != "GET" or (parsed.scheme, parsed.netloc) != (
                origin.scheme,
                origin.netloc,
            ):
                violations.append("Blocked a write or nonlocal request.")
                await route.abort("blockedbyclient")
                return
            if parsed.path == "/api" or parsed.path.startswith("/api/"):
                try:
                    item = frozen[request_key(request.method, request.url)]
                except (KeyError, AccessError):
                    violations.append("Blocked an API request absent from the snapshot.")
                    await route.abort("blockedbyclient")
                    return
                await route.fulfill(
                    status=item["status"],
                    content_type="application/json",
                    body=json.dumps(item["body"]),
                )
                return
            allowed = (
                parsed.path == "/mcp-render.html"
                or parsed.path == "/@vite/client"
                or parsed.path == "/@react-refresh"
                or parsed.path.startswith(("/assets/", "/src/", "/node_modules/"))
            )
            if (
                not allowed
                or any(part in {".", ".."} for part in parsed.path.split("/"))
                or "%" in parsed.path
                or "\\" in parsed.path
            ):
                await route.abort("blockedbyclient")
                return
            # Fetch assets ourselves so a dev-server redirect cannot escape the origin.
            try:
                response = await route.fetch(max_redirects=0, timeout=self.timeout_ms)
                if 300 <= response.status < 400:
                    violations.append("Blocked an asset redirect.")
                    await route.abort("blockedbyclient")
                else:
                    content = await response.body()
                    asset_bytes += len(content)
                    if len(content) > 16 * 1024 * 1024 or asset_bytes > 64 * 1024 * 1024:
                        violations.append("Renderer assets exceed the bounded loading budget.")
                        await route.abort("blockedbyclient")
                        return
                    asset_hashes[parsed.path] = hashlib.sha256(content).hexdigest()
                    await route.fulfill(response=response)
            except Error:
                await route.abort("failed")

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(
                        viewport=VIEWPORT,
                        device_scale_factor=1,
                        service_workers="block",
                        accept_downloads=False,
                    )
                    await context.route("**/*", route_request)
                    await context.route_web_socket("**/*", lambda socket: socket.close())
                    payload = {
                        **envelope,
                        "snapshot_id": snapshot_id,
                        "settings": settings,
                        "clock": envelope["context"]["clock_response"],
                    }
                    await context.add_init_script(
                        "Object.defineProperty(window,'__ALPHA_DECK_MCP_SNAPSHOT__',{value:"
                        + json.dumps(payload)
                        + ",writable:false,configurable:false});"
                    )
                    page = await context.new_page()
                    page.set_default_timeout(self.timeout_ms)
                    await page.goto(
                        self.ui_base + "/mcp-render.html",
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
                    await page.wait_for_function(
                        "() => ['ready','error'].includes(window.__ALPHA_DECK_MCP_VIEW__?.status)",
                        timeout=self.timeout_ms,
                    )
                    view = await page.evaluate("window.__ALPHA_DECK_MCP_VIEW__")
                    if view.get("status") != "ready":
                        if view.get("error_code") == "invalid_view_state":
                            raise AccessError(
                                "invalid_view_state",
                                "Requested selection is absent from this captured dashboard; inspect data or use default settings.",
                            )
                        raise AccessError(
                            "render_failed",
                            "Dashboard reported an observation/render error.",
                            retryable=True,
                        )
                    if view.get("snapshot_id") != snapshot_id or view.get("settings") != settings:
                        raise AccessError(
                            "render_mismatch",
                            "Dashboard bridge did not acknowledge the captured snapshot and settings.",
                        )
                    if violations:
                        raise AccessError("blocked_request", violations[0])
                    root = page.locator("[data-mcp-root]")
                    if await root.count() != 1:
                        raise AccessError(
                            "render_failed", "Dashboard did not expose exactly one capture root."
                        )
                    locator = root
                    if target_id is not None:
                        # JSON string interpolation is an expression argument, never a CSS selector.
                        handle = await page.evaluate_handle(
                            "id => [...document.querySelectorAll('[data-mcp-id]')].find(el => el.getAttribute('data-mcp-id') === id)",
                            target_id,
                        )
                        locator = handle.as_element()
                        if locator is None or not await locator.is_visible():
                            raise AccessError(
                                "target_not_visible",
                                "Requested target is not visible in this captured state.",
                            )
                        await locator.scroll_into_view_if_needed()
                        await page.evaluate(
                            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                        )
                    geometry = await locator.evaluate("""el => {
                        const rect=el.getBoundingClientRect();
                        const box={x:rect.x,y:rect.y,width:rect.width,height:rect.height};
                        let left=Math.max(0,rect.left),top=Math.max(0,rect.top),right=Math.min(innerWidth,rect.right),bottom=Math.min(innerHeight,rect.bottom);
                        for(let p=el.parentElement;p;p=p.parentElement){
                            const s=getComputedStyle(p),r=p.getBoundingClientRect();
                            if(/hidden|auto|scroll|clip/.test(s.overflowX)){left=Math.max(left,r.left+p.clientLeft);right=Math.min(right,r.left+p.clientLeft+p.clientWidth)}
                            if(/hidden|auto|scroll|clip/.test(s.overflowY)){top=Math.max(top,r.top+p.clientTop);bottom=Math.min(bottom,r.top+p.clientTop+p.clientHeight)}
                        }
                        return {box,viewport_clip:{x:left,y:top,width:right-left,height:bottom-top},scroll:{x:scrollX,y:scrollY}};
                    }""")
                    box, visible_clip, scroll = (
                        geometry["box"],
                        geometry["viewport_clip"],
                        geometry["scroll"],
                    )
                    if not box:
                        raise AccessError(
                            "target_not_visible", "Requested capture has no visible bounds."
                        )
                    width, height = visible_clip["width"], visible_clip["height"]
                    if width <= 0 or height <= 0:
                        raise AccessError(
                            "target_not_visible", "Requested capture is outside the viewport."
                        )
                    document_clip = {
                        "x": visible_clip["x"] + scroll["x"],
                        "y": visible_clip["y"] + scroll["y"],
                        "width": width,
                        "height": height,
                    }
                    png = await page.screenshot(
                        type="png",
                        clip=visible_clip,
                        animations="disabled",
                        timeout=self.timeout_ms,
                    )
                    if len(png) > MAX_PNG_BYTES:
                        raise AccessError("image_too_large", "Screenshot exceeds the 8 MiB limit.")
                    metadata = {
                        "viewport": dict(VIEWPORT),
                        "clip": visible_clip,
                        "document_clip": document_clip,
                        "viewport_clip": visible_clip,
                        "page_scroll": scroll,
                        "element_bounds": box,
                        "cropped": any(
                            abs(visible_clip[key] - box[key]) > 0.5 for key in visible_clip
                        ),
                        "full_page": False,
                        "target_id": target_id,
                        "format": "png",
                        "asset_digest": hashlib.sha256(
                            json.dumps(asset_hashes, sort_keys=True).encode()
                        ).hexdigest(),
                    }
                    return view, png, metadata
                finally:
                    await browser.close()
        except TimeoutError:
            raise AccessError(
                "render_timeout",
                "Dashboard did not become ready before the rendering deadline.",
                retryable=True,
            ) from None
        except Error as error:
            logging.getLogger(__name__).warning(
                "browser_error exception_class=%s", type(error).__name__
            )
            raise AccessError(
                "browser_unavailable",
                "Local Chromium could not render the dashboard; check the UI and Playwright browser installation.",
                retryable=True,
            ) from None
