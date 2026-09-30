import re
import shutil
import subprocess

import pytest
from django.template.loader import get_template


@pytest.fixture
def page(client):
    response = client.get("/map/")
    assert response.status_code == 200
    return response, response.content.decode()


def test_map_page_renders_from_the_app_template(page):
    response, _ = page
    assert [t.name for t in response.templates] == ["routing/map.html"]
    assert get_template("routing/map.html").origin.name.replace("\\", "/").endswith(
        "routing/templates/routing/map.html"
    )


def test_map_page_loads_pinned_leaflet_with_integrity_hashes(page):
    _, html = page
    assert '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"' in html
    assert '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"' in html
    versions = set(re.findall(r"leaflet@([^/]+)/", html))
    assert versions == {"1.9.4"}  # pinned, never "latest"
    assert html.count('integrity="sha256-') == 2


def test_map_page_has_prefilled_inputs_button_and_status_areas(page):
    _, html = page
    assert re.search(r'<input id="start"[^>]*value="Chicago, IL"', html)
    assert re.search(r'<input id="finish"[^>]*value="Los Angeles, CA"', html)
    assert re.search(r'<button id="plan-btn" type="submit">Plan route</button>', html)
    assert 'id="status"' in html and 'id="error"' in html and 'id="summary"' in html
    assert 'id="map"' in html and 'id="stops-table"' in html and "<tfoot>" in html


def test_map_page_posts_to_the_route_endpoint_and_credits_openstreetmap(page):
    _, html = page
    assert 'data-api-url="/api/route/"' in html
    assert "https://tile.openstreetmap.org/{z}/{x}/{y}.png" in html  # {% verbatim %} kept JS intact
    assert "OpenStreetMap</a> contributors" in html


def test_map_page_never_writes_server_strings_as_html(page):
    _, html = page
    for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert unsafe not in html


def test_root_redirects_to_the_map(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == "/map/"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_script_is_valid_javascript(page, tmp_path):
    _, html = page
    script = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    path = tmp_path / "map_inline.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
