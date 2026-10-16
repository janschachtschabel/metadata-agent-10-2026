"""
The shipped widget bundle in a real browser, embedded the way a host does it.

Regression tests for what the staging and production consoles showed:

- a second evaluation of the bundle threw NotSupportedError,
- an Angular host's <app-root> switched the widget into standalone mode,
- without api-url the widget called the host page's origin, not the agent,
- fonts were loaded from Google,

and for what the widget's stylesheet must not do to its host: put Material
tokens on <html> (edu-sharing runs Angular Material itself), or replace the
host's body font and icon font.

The host page lives on another origin than the agent, which sits behind a
proxy prefix like edu-sharing's bapi proxy. Requests are answered in-process —
agent requests by this backend, the host page from a string — and everything
else is recorded and aborted. Playwright's route.fulfill adds CORS headers by
itself, so CORS is covered in test_cors.py instead. Skips when Chromium is not
installed.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from src import main

async_api = pytest.importorskip("playwright.async_api")

HOST = "https://host.example"
PROXY = "/edu-sharing/rest/bapi/api/v1/proxy/metadata-agent-canvas"
AGENT = f"https://redaktion.example{PROXY}"
SCRIPTS = ("runtime", "polyfills", "main")
DIST = Path(__file__).resolve().parents[1] / "src/static/widget/dist"

# The host's own styles come first, as a host's do: a global rule of the widget
# stylesheet with the same specificity would win over them.
HOST_STYLES = (
    "<style>body { font-family: Georgia, serif; }"
    " .material-icons { font-family: 'Host Icons'; }</style>"
)
HOST_ICON = "<span class='material-icons' id='host-icon'>home</span>"


class Embedding:
    """A host page with one widget; records requests, console errors and logs."""

    def __init__(self, page, body: str):
        self.page = page
        self.body = body
        self.requests: list[str] = []
        self.errors: list[str] = []
        self.logs: list[str] = []
        self.agent = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://agent.test"
        )
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("console", self._console)

    def _console(self, message):
        if message.type == "error":
            self.errors.append(message.text)
        else:
            self.logs.append(message.text)

    async def _route(self, route):
        request = route.request
        self.requests.append(request.url)
        parts = urlsplit(request.url)
        if request.url == f"{HOST}/":
            await route.fulfill(body=self.body, content_type="text/html")
        elif request.url.startswith(AGENT):
            target = parts.path[len(PROXY) :] + (
                f"?{parts.query}" if parts.query else ""
            )
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in {"host", "accept-encoding", "content-length"}
            }
            response = await self.agent.request(
                request.method,
                target,
                headers=headers,
                content=request.post_data_buffer,
            )
            await route.fulfill(
                status=response.status_code,
                headers={
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() not in {"content-encoding", "content-length"}
                },
                body=response.content,
            )
        else:
            await route.abort()

    async def open(self, script_sets: int = 1):
        await self.page.route("**/*", self._route)
        await self.page.goto(f"{HOST}/")
        for _ in range(script_sets):
            for name in SCRIPTS:
                await self.page.add_script_tag(url=f"{AGENT}/widget/dist/{name}.js")
        await self.page.wait_for_selector("metadata-agent-canvas.ready", timeout=30000)

    async def close(self):
        await self.agent.aclose()


def _host_page(extra: str = "", layout: str = "default") -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{HOST_STYLES}"
        f"<link rel='stylesheet' href='{AGENT}/widget/dist/styles.css'>"
        f"</head><body>{HOST_ICON}{extra}<metadata-agent-canvas layout='{layout}'>"
        "</metadata-agent-canvas></body></html>"
    )


@pytest.fixture
async def browser():
    if not (DIST / "main.js").exists():
        pytest.skip("widget bundle not built")
    async with async_api.async_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Chromium missing: uv run playwright install chromium")
        instance = await playwright.chromium.launch()
        try:
            yield instance
        finally:
            await instance.close()


@pytest.fixture
async def embed(browser):
    embeddings = []

    async def make(
        extra: str = "", script_sets: int = 1, layout: str = "default"
    ) -> Embedding:
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        embedding = Embedding(page, _host_page(extra, layout))
        embeddings.append(embedding)
        await embedding.open(script_sets)
        return embedding

    yield make
    for embedding in embeddings:
        await embedding.close()


async def test_loading_the_widget_scripts_twice_registers_the_element_once(embed):
    embedding = await embed(script_sets=2)

    assert embedding.errors == []
    assert sum(line.startswith("✅ Web Component") for line in embedding.logs) == 1
    # The skipped bundle says so, naming the running and the skipped version.
    skipped = [line for line in embedding.logs if "skipping this bundle" in line]
    assert len(skipped) == 1
    assert re.search(r"v(\d+\.\d+\.\d+) is already registered.*\(v\1\)", skipped[0])


async def test_a_foreign_angular_app_root_does_not_block_the_widget(embed):
    embedding = await embed(extra="<app-root></app-root>")

    assert embedding.errors == []
    assert (
        await embedding.page.evaluate(
            "document.querySelector('app-root').childElementCount"
        )
        == 0
    )


async def test_without_configuration_the_widget_calls_the_agent_it_was_loaded_from(
    embed,
):
    embedding = await embed()

    assert any(url.startswith(f"{AGENT}/widget/i18n/") for url in embedding.requests)
    assert any(url.startswith(f"{AGENT}/info/schema/") for url in embedding.requests)
    assert [url for url in embedding.requests if url.startswith(HOST)] == [f"{HOST}/"]


async def test_every_request_goes_to_the_host_page_or_the_agent(embed):
    """No font CDN, no Vercel, no PageShot — and nothing else outside either."""
    embedding = await embed()
    await embedding.page.evaluate("document.fonts.ready")

    elsewhere = [
        url
        for url in embedding.requests
        if url != f"{HOST}/" and not url.startswith(AGENT)
    ]
    assert elsewhere == []


async def test_the_self_hosted_fonts_load_and_icons_render_as_glyphs(embed):
    embedding = await embed()
    fonts = await embedding.page.evaluate(
        """async () => {
            await document.fonts.ready;
            const icon = document.querySelector('metadata-agent-canvas mat-icon');
            return {
                loaded: [...document.fonts].filter(f => f.status === 'loaded')
                    .map(f => f.family.replace(/"/g, '')),
                iconWidth: icon.getBoundingClientRect().width,
                iconSize: parseFloat(getComputedStyle(icon).fontSize),
            };
        }"""
    )

    assert "Metadata Agent Material Icons" in fonts["loaded"]
    assert "Metadata Agent Roboto" in fonts["loaded"]
    # A ligature is one glyph; an unstyled icon name would be a word wide.
    assert fonts["iconWidth"] <= 1.5 * fonts["iconSize"]
    assert any(
        url.startswith(f"{AGENT}/widget/dist/fonts/") for url in embedding.requests
    )


async def test_the_widget_styles_leave_the_host_page_alone(embed):
    embedding = await embed()
    styles = await embedding.page.evaluate(
        """() => {
            const css = el => getComputedStyle(el);
            const tokens = el => ['--mat-form-field-container-height',
                                  '--mat-slide-toggle-selected-handle-size',
                                  '--mat-sys-on-surface']
                .map(name => css(el).getPropertyValue(name).trim());
            return {
                html: tokens(document.documentElement),
                widget: tokens(document.querySelector('metadata-agent-canvas')),
                bodyFont: css(document.body).fontFamily,
                hostIconFont: css(document.getElementById('host-icon')).fontFamily,
            };
        }"""
    )

    assert styles["html"] == ["", "", ""]
    assert styles["widget"][:2] == ["56px", "24px"]
    assert styles["bodyFont"] == "Georgia, serif"
    assert styles["hostIconFont"] == '"Host Icons"'


@pytest.mark.parametrize("layout", ["default", "plugin"])
async def test_the_preview_switch_handle_stays_inside_its_track(embed, layout):
    """Two Material themes in one stylesheet moved the handle onto its label."""
    embedding = await embed(layout=layout)
    geometry = await embedding.page.evaluate(
        """() => [...document.querySelectorAll('metadata-agent-canvas mat-slide-toggle')]
            .map(toggle => [toggle.querySelector('.mdc-switch__track'),
                            toggle.querySelector('.mdc-switch__handle')]
                .map(el => el.getBoundingClientRect())
                .map(r => ({left: r.left, right: r.right})))"""
    )

    # These layouts show the preview switch; the other five do not.
    assert geometry, f"layout '{layout}' shows no switch"
    for track, handle in geometry:
        assert track["left"] <= handle["left"] and handle["right"] <= track["right"], (
            f"handle {handle} outside track {track}"
        )
