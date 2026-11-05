"""Render real dashboard DOM against one frozen capture, with no live API fallback."""

import hashlib
import json
import logging
from urllib.parse import urlsplit

import httpx

from .capture import loopback_base, request_key, response_key
from .errors import AccessError

VIEWPORT = {"width": 1440, "height": 1080}
MAX_PNG_BYTES = 8 * 1024 * 1024
# The renderer entry's own title (frontend/mcp-render.html), identical in Vite dev and the build.
# A UI answering 200 without it serves some other page: an older build or a different app.
RENDERER_MARKER = "<title>Alpha Deck captured dashboard</title>"
PREFLIGHT_TIMEOUT_S = 5.0
MAX_PREFLIGHT_BYTES = 1024 * 1024
BROWSER_NOT_INSTALLED = (
    "Chromium for Playwright is not installed on this machine. Using the MCP server's Python, "
    "run: python -m playwright install --only-shell chromium"
)
OCCLUSION_CHECK = """(el, clip) => {
    // Inert and pointer-events affect hit testing, not pixels. Lift them only during
    // this synchronous measurement; no interactions run and every change is restored.
    const inertNodes=document.querySelectorAll('[inert]');
    const nodes=document.querySelectorAll('*');
    if(inertNodes.length>64||nodes.length>50000) return {strategy:'clipped_grid_hit_test',checked:false};
    const inert=[...inertNodes].map(node=>[node,node.getAttribute('inert')]);
    const sheet=document.createElement('style');
    sheet.textContent='* { pointer-events: auto !important; }';
    let samples=0,covered=0,background=0,budget=10000,checked=true; const owners=new Set(),overrides=[];
    try {
        for(const [node] of inert) node.removeAttribute('inert');
        document.head.appendChild(sheet);
        for(const node of nodes) {
            if(getComputedStyle(node).pointerEvents==='none') {
                if(overrides.length>=2048||!node.style) return {strategy:'clipped_grid_hit_test',checked:false};
                overrides.push([node,node.getAttribute('style')]);
                node.style.setProperty('pointer-events','auto','important');
            }
        }
        samplesLoop: for(let row=0;row<5;row++) for(let col=0;col<5;col++) {
            const positions=size=>[Math.min(.5,size/2),size*.25,size*.5,size*.75,Math.max(size/2,size-.5)];
            const x=clip.x+positions(clip.width)[col],y=clip.y+positions(clip.height)[row];
            let top=null;
            const stack=document.elementsFromPoint(x,y);
            if(stack.length>64){checked=false;break samplesLoop;}
            for(const node of stack) {
                let eligible=true,depth=0;
                for(let parent=node;parent;parent=parent.parentElement) {
                    if(--budget<0||++depth>64){checked=false;break samplesLoop;}
                    const style=getComputedStyle(parent);
                    if(style.visibility==='hidden'||style.visibility==='collapse'||Number(style.opacity)===0){eligible=false;break;}
                }
                if(eligible){top=node;break;}
            }
            samples++;
            if(top && (top===el || el.contains(top))) {
                const owner=top.closest('[data-mcp-id]')?.getAttribute('data-mcp-id'); if(owner) owners.add(owner);
            } else if(!top || top.contains(el)) background++;
            else covered++;
        }
    } finally {
        sheet.remove();
        for(const [node,value] of overrides) { if(value===null) node.removeAttribute('style'); else node.setAttribute('style',value); }
        for(const [node,value] of inert) node.setAttribute('inert',value??'');
    }
    return {strategy:'clipped_grid_and_perimeter_hit_test',assurance:'sampled_best_effort',checked,
        limitation:'Unsampled thin interior coverage may not be detected; this does not prove every pixel is unobscured.',
        sample_count:samples,covered_points:covered,target_owned_points:samples-covered-background,
        background_points:background,
        sampled_fully_covered:covered===samples,sampled_partially_covered:covered>0&&covered<samples,
        hit_target_ids:[...owners]};
}"""


def launch_failure(error: Exception) -> AccessError:
    """Map a Chromium launch failure to a stable error; preflight and render share this one rule.

    A launch message names a local executable path, never captured content, so its first line
    is safe to log. Errors raised later, while a page renders captured data, are not routed here.
    """
    message = str(error)
    first_line = (message.strip().splitlines() or [""])[0][:200]
    logging.getLogger(__name__).warning(
        "browser_launch_error exception_class=%s message=%s", type(error).__name__, first_line
    )
    if "Executable doesn't exist" in message:
        return AccessError("browser_not_installed", BROWSER_NOT_INSTALLED, retryable=False)
    return AccessError(
        "browser_unavailable",
        "Local Chromium could not start; check the Playwright browser installation.",
        retryable=True,
    )


class FrozenRenderer:
    def __init__(
        self,
        ui_base: str,
        *,
        timeout_ms: int = 40000,
        preflight_timeout: float = PREFLIGHT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
        playwright_factory=None,
    ):
        self.ui_base = loopback_base(ui_base)
        self.timeout_ms = timeout_ms
        self.preflight_timeout = preflight_timeout
        self.transport = transport
        # Injectable for synthetic tests; the default is Playwright's async_playwright.
        self.playwright_factory = playwright_factory
        self._browser_ready = False

    def _playwright(self):
        if self.playwright_factory is not None:
            return self.playwright_factory()
        from playwright.async_api import async_playwright

        return async_playwright()

    async def _launch(self, playwright):
        from playwright.async_api import Error

        try:
            return await playwright.chromium.launch(headless=True)
        except Error as error:
            self._browser_ready = False  # A browser that stopped launching is re-checked next read.
            raise launch_failure(error) from None

    async def preflight(self) -> None:
        """Fail fast before a capture: the UI must serve the renderer and Chromium must launch.

        The UI is checked on every call. A successful browser launch is remembered for this
        renderer's lifetime; a failure never is, so installing Chromium needs no restart.
        """
        await self._check_ui()
        if not self._browser_ready:
            await self._check_browser()
            self._browser_ready = True

    async def _check_ui(self) -> None:
        def unavailable(detail: str = "") -> AccessError:
            # Down or failing: a retry can succeed once the frontend recovers.
            return AccessError(
                "ui_unavailable",
                f"The Alpha Deck frontend at {self.ui_base} is not answering{detail}; start it,"
                " or point --ui-base at a running frontend.",
                retryable=True,
            )

        def not_served(detail: str = "") -> AccessError:
            # It answered, but not with the renderer: a retry gets the same answer.
            return AccessError(
                "renderer_not_served",
                f"The frontend at {self.ui_base} serves a page that is not the Alpha Deck"
                f" renderer{detail}: its frontend build predates the MCP renderer, or it is a"
                " different app. Rebuild that frontend, or point --ui-base at one that serves it.",
            )

        content = bytearray()
        try:
            async with httpx.AsyncClient(
                timeout=self.preflight_timeout,
                trust_env=False,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                async with client.stream("GET", self.ui_base + "/mcp-render.html") as response:
                    status = response.status_code
                    if status != 200:
                        detail = f" (HTTP {status} for /mcp-render.html)"
                        raise unavailable(detail) if status >= 500 else not_served(detail)
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_PREFLIGHT_BYTES:
                            break
        except httpx.HTTPError:
            raise unavailable() from None
        if RENDERER_MARKER.encode("utf-8") not in content:
            raise not_served()

    async def _check_browser(self) -> None:
        from playwright.async_api import Error

        try:
            async with self._playwright() as playwright:
                browser = await self._launch(playwright)
                await browser.close()
        except Error as error:
            raise launch_failure(error) from None

    async def render(
        self, envelope: dict, snapshot_id: str, settings: dict, target_id: str | None = None
    ) -> tuple[dict, bytes, dict]:
        from playwright.async_api import Error, TimeoutError

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
            async with self._playwright() as playwright:
                browser = await self._launch(playwright)
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
                    occlusion = None
                    if target_id is not None:
                        occlusion = await locator.evaluate(OCCLUSION_CHECK, visible_clip)
                        if not occlusion["checked"]:
                            raise AccessError(
                                "occlusion_check_failed",
                                "Requested target exceeded the bounded screenshot visibility check.",
                            )
                        if occlusion["covered_points"]:
                            raise AccessError(
                                "target_occluded",
                                "Requested target is covered by another element; choose an exposed target or a captured view that reveals it.",
                            )
                        if not occlusion["target_owned_points"]:
                            raise AccessError(
                                "target_not_visible",
                                "No exposed target content was found within the sampled screenshot region.",
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
                        "cropping_basis": "viewport_and_overflow_geometry",
                        "full_page": False,
                        "target_id": target_id,
                        "occlusion": occlusion,
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
