"""Bundled UI delivery uses the same security boundary as the controller API."""
from importlib.resources import files

import pytest

from test_controller_security import API, bootstrap, controller  # noqa: F401


def test_setup_document_and_assets_are_available_before_bootstrap(controller):
    _, client, _ = controller
    root = client.get("/")
    assert root.status_code == 200
    assert '<html lang="en">' in root.text
    assert 'name="viewport"' in root.text
    assert 'src="/assets/app.js"' in root.text
    assert 'href="/assets/styles.css"' in root.text
    for name, media_type in [("app.js", "javascript"), ("styles.css", "text/css")]:
        response = client.get("/assets/" + name)
        assert response.status_code == 200
        assert media_type in response.headers["content-type"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(API + "/status").status_code == 401


def test_csp_allows_only_bundled_code_and_styles(controller):
    _, client, _ = controller
    policy = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy


@pytest.mark.parametrize("path", ["/assets/unknown.js", "/assets/index.html", "/assets/../controller/app.py", "/assets/%2e%2e/controller/app.py"])
def test_assets_are_an_exact_allowlist_not_a_directory(controller, path):
    app, client, _ = controller
    assert client.get(path).status_code in {401, 404}
    bootstrap(app, client)
    assert client.get(path).status_code == 404


def test_assets_reject_foreign_hosts_origins_and_writes(controller):
    _, client, _ = controller
    assert client.get("/assets/app.js", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/assets/styles.css", headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/assets/app.js").status_code == 403


def test_assets_are_package_resources():
    root = files("h3server").joinpath("web")
    for name in ("index.html", "app.js", "styles.css"):
        assert root.joinpath(name).is_file()
    script = root.joinpath("app.js").read_text()
    assert "history.replaceState" in script
    assert script.index("history.replaceState") < script.index("fetch(")
    assert "localStorage" not in script and "sessionStorage" not in script
    assert "innerHTML" not in script and "console.log" not in script
