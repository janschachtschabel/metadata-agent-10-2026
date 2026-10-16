"""
/widget/info must describe the widget that is actually shipped.

The endpoint used to advertise attributes and events that did not exist in the
bundle, which sent integrators down dead ends. These tests read the shipped
bundle and fail when the endpoint drifts away from it again.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import main

DIST = Path(__file__).resolve().parents[1] / "src/static/widget/dist"
BUNDLE = DIST / "main.js"
STYLES = DIST / "styles.css"
EXAMPLES = Path(__file__).resolve().parents[1] / "src/static/widget/examples"
EXTERNAL_HOSTS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "vercel.app",
    "pageshot.site",
)


@pytest.fixture(scope="module")
def bundle_source() -> str:
    if not BUNDLE.exists():
        pytest.skip(f"widget bundle not built: {BUNDLE}")
    return BUNDLE.read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def info() -> dict:
    with TestClient(main.app) as client:
        response = client.get("/widget/info")
    assert response.status_code == 200
    return response.json()


def test_every_advertised_event_exists_in_the_bundle(info, bundle_source):
    for event in info["events"]:
        assert event in bundle_source, (
            f"/widget/info advertises event '{event}' which the bundle does not emit"
        )


def test_every_advertised_layout_exists_in_the_bundle(info, bundle_source):
    for layout in info["layouts"]:
        assert f'"layout-{layout}"' in bundle_source, (
            f"/widget/info advertises layout '{layout}' which the bundle does not define"
        )


def test_every_advertised_attribute_exists_in_the_bundle(info, bundle_source):
    """Attributes are kebab-case; Angular Elements derives them from camelCase inputs."""
    for attribute in info["attributes"]:
        parts = attribute.split("-")
        camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
        assert f"{camel}:" in bundle_source, (
            f"/widget/info advertises attribute '{attribute}' "
            f"(input '{camel}') which the bundle does not accept"
        )


def test_response_keeps_its_documented_top_level_shape(info):
    for key in ("name", "dist_base_url", "scripts", "variants", "events", "examples"):
        assert key in info


def test_widget_examples_keep_reverse_proxy_prefix():
    """Example runtime config must retain a path-based proxy mount."""
    offenders = [
        path.name
        for path in EXAMPLES.glob("*.html")
        if "agentUrl: window.location.origin" in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        "These widget examples discard a reverse-proxy path prefix: "
        + ", ".join(offenders)
    )


@pytest.fixture(scope="module")
def styles_source() -> str:
    if not STYLES.exists():
        pytest.skip(f"widget styles not built: {STYLES}")
    return STYLES.read_text(encoding="utf-8", errors="ignore")


@pytest.mark.parametrize(
    "name", ["main.js", "polyfills.js", "runtime.js", "styles.css"]
)
def test_shipped_widget_files_reference_no_external_host(name):
    """No Google Fonts, no Vercel backend, no PageShot — in any shipped file."""
    path = DIST / name
    if not path.exists():
        pytest.skip(f"widget not built: {path}")
    source = path.read_text(encoding="utf-8", errors="ignore")

    found = [host for host in EXTERNAL_HOSTS if host in source]
    assert not found, f"{name} references {found}"


def test_styles_define_the_icon_ligatures_google_used_to_provide(styles_source):
    """
    Font files alone are not enough — without these rules icons render as text.

    They hold inside the widget only and name the widget's own font families,
    so icons on the host page keep the host's icon font.
    """
    compact = re.sub(r"[\s\"']", "", styles_source)
    assert "metadata-agent-canvas.material-icons," in compact
    assert "{font-family:MetadataAgentMaterialIcons}" in compact
    assert "{font-family:MetadataAgentMaterialIconsOutlined!important}" in compact
    assert "font-feature-settings:liga" in compact
    # A global rule would start right after the previous one: "}.material-icons".
    assert not re.search(r"(^|\})\.material-icons", styles_source)


def test_demo_pages_with_their_own_icons_bring_the_icon_styles():
    """styles.css keeps .material-icons inside the widget; a page's own icons
    would otherwise show their names as text."""
    without = [
        page.name
        for page in EXAMPLES.glob("*.html")
        if 'class="material-icons' in (text := page.read_text(encoding="utf-8"))
        and 'href="demo-icons.css"' not in text
    ]
    assert without == []
    assert (EXAMPLES / "demo-icons.css").is_file()


def test_every_font_the_stylesheet_names_is_served_as_woff2(styles_source):
    fonts = sorted(set(re.findall(r"url\((fonts/[\w.-]+\.woff2)\)", styles_source)))
    assert len(fonts) == 8, fonts  # 2 icon fonts + Roboto 300/400/500 x 2 subsets

    with TestClient(main.app) as client:
        for font in fonts:
            response = client.get(f"/widget/dist/{font}")
            assert response.status_code == 200, font
            assert response.headers["content-type"] == "font/woff2", font
            assert response.content


def test_widget_info_advertises_no_external_font_stylesheet(info):
    """The fonts arrive with styles.css; nothing else has to be linked."""
    assert info["scripts"]["fonts"] == []
    assert any(
        url.endswith("/widget/dist/styles.css") for url in info["scripts"]["styles"]
    )


def test_no_example_page_loads_google_fonts():
    offenders = [
        path.name
        for path in EXAMPLES.glob("*.html")
        if any(host in path.read_text(encoding="utf-8") for host in EXTERNAL_HOSTS)
    ]

    assert not offenders, f"external hosts in widget examples: {offenders}"


def test_the_shipped_bundle_matches_the_backend_version(bundle_source, info):
    """/widget/info reports the backend version as the widget version."""
    version = info["version"]
    # The minifier either inlines the version into the registration message or
    # keeps it in a constant that the message interpolates.
    constant = re.search(r'([\w$]+)="' + re.escape(version) + '"', bundle_source)
    assert f"v{version} registered" in bundle_source or (
        constant is not None
        and f"v${{{constant.group(1)}}} registered" in bundle_source
    )
