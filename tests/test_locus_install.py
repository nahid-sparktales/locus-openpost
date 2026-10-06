"""Exercise the real Locus installer and MCP panel bridge against this package.

Run with LOCUS_SOURCE=/path/to/locus. Set LOCUS_PLUGIN_SOURCE to the public
HTTPS Git URL to verify downloading the published package too. All extension
state, subprocess HOME, project paths, and plugin data stay in pytest's tmpdir.
No credential, publishing, clipboard, export-dialog, or browser tools are used.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("LOCUS_SOURCE"), reason="Set LOCUS_SOURCE to a Locus checkout for host integration tests",
)
PACKAGE = Path(__file__).resolve().parents[1]


def clean_package(destination):
    shutil.copytree(PACKAGE, destination, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "*.pyc", "*.pyo", "node_modules", ".pytest_cache",
        ".venv", "venv", "test-results", "playwright-report", ".artifacts",
        "coverage", ".coverage", ".DS_Store", "*.log",
    ))
    return destination


@pytest.fixture
def host(tmp_path, monkeypatch):
    source = Path(os.environ["LOCUS_SOURCE"]).expanduser().resolve()
    assert (source / "agent/ollama_code/extensions.py").is_file(), "LOCUS_SOURCE must name the Locus repository"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OLLAMA_CODE_HOME", str(home / "agent"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.syspath_prepend(str(source / "agent"))
    # Preserve any explicitly supplied bundled SDK site-packages path in the
    # environment: the real package requests PYTHONPATH for that interpreter.
    extensions = importlib.import_module("ollama_code.extensions")
    runtime_module = importlib.import_module("ollama_code.mcp_runtime")
    endpoint = importlib.import_module("ollama_code.api.extensions").call_extension_plugin_panel_tool
    selected = tmp_path / "selected-chat"
    projects = [tmp_path / "project-a", tmp_path / "project-b"]
    for project in [selected, *projects]:
        project.mkdir()
    manager = extensions.ExtensionManager(str(selected), root=tmp_path / "extensions")
    runtime = runtime_module.MCPManager(manager)
    service = SimpleNamespace(core=SimpleNamespace(cwd=str(selected), extensions=manager, mcp=runtime))
    result = SimpleNamespace(
        root=tmp_path, home=home, manager=manager, runtime=runtime, service=service,
        endpoint=endpoint, extensions=extensions, runtime_class=runtime_module.MCPManager,
        projects=projects, selected=selected,
    )
    yield result
    result.runtime.close()


def install(host, *, remote=False):
    marketplace = host.root / "marketplace"
    plugin_source = os.environ.get("LOCUS_PLUGIN_SOURCE", "") if remote else ""
    if plugin_source:
        parsed = urlparse(plugin_source)
        assert parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password, \
            "LOCUS_PLUGIN_SOURCE must be a credential-free HTTPS Git URL"
        source = {"source": "url", "url": plugin_source}
        package = None
    else:
        package = clean_package(marketplace / "packages/social-studio")
        source = {"source": "local", "path": "./packages/social-studio"}
    catalog = marketplace / ".agents/plugins/marketplace.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({"name": "Integration test", "plugins": [{
        "name": "social-studio", "source": source,
    }]}))
    registered = host.manager.add_marketplace(str(marketplace))
    inspection = host.manager.inspect_catalog_plugin(registered["id"], "social-studio")
    assert inspection["plugin"]["version"] == "0.2.0"
    assert inspection["plugin"]["screens"] == []
    assert inspection["trust"]["panels"][0]["tools"] == ["social_studio"]
    with pytest.raises(host.extensions.ExtensionError, match="trust-review digest"):
        host.manager.install_plugin(registered["id"], "social-studio")
    installed = host.manager.install_plugin(
        registered["id"], "social-studio", expected_digest=inspection["digest"],
        scope="workspace", workspace=str(host.projects[0]),
    )
    assert Path(installed["root"]).is_relative_to(host.root)
    assert installed["digest"] == inspection["digest"]
    assert not (Path(installed["root"]) / ".git").exists()
    parsed = host.extensions.parse_plugin(Path(installed["root"]))
    assert parsed["panels"][0]["entrypoint"] == "ui/index.html"
    assert parsed["mcp_servers"][0]["command"] == "${LOCUS_PYTHON}"
    return installed, registered, package


def request(host, installed, project, operation, payload=None):
    return {
        "plugin_id": installed["id"], "panel_id": "social-studio", "digest": installed["digest"],
        "workspace": str(project), "tool": "social_studio",
        "arguments": {"operation": operation, "payload": payload or {}},
    }


def call(host, installed, project, operation, **payload):
    response = host.endpoint(host.service, request(host, installed, project, operation, payload))
    assert response["is_error"] is False, response["content"]
    return json.loads(response["content"])


def assert_refused(host, body, status):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as rejected:
        host.endpoint(host.service, body)
    assert rejected.value.status_code == status


def test_real_package_install_two_projects_restart_and_revocation(host):
    installed, marketplace, _package = install(host, remote=True)
    first, second = host.projects
    host.manager.set_plugin_enabled(installed["id"], True, scope="workspace", workspace=str(second))
    for project in host.projects:
        initial = call(host, installed, project, "state")
        assert initial["workspace"] == str(project.resolve()) and initial["total"] == 0
        assert initial["connection"] is None and initial["legacySources"] == []
    draft = call(host, installed, first, "draft_create")["draft"]
    for field, value in [
        ("title", "Release notes"), ("text", "A useful improvement for our customers."),
        ("channels", ["linkedin", "x"]), ("variant:x", "A useful improvement."),
        ("plannedAt", "2099-01-02T09:30:00-05:00"),
    ]:
        draft = call(host, installed, first, "draft_update", id=draft["id"], field=field, value=value)["draft"]
    assert draft["plannedAt"] == "2099-01-02T14:30:00Z"
    for field, value in [("name", "Project A"), ("voice", "Direct and practical")]:
        call(host, installed, first, "brand_update", field=field, value=value)
    prompt = call(host, installed, first, "assistant_prompt", action="adapt", id=draft["id"])["prompt"]
    assert "Project A" in prompt and draft["text"] in prompt and "Direct and practical" in prompt
    research = call(host, installed, first, "assistant_prompt", action="research", topic="launch writing")["prompt"]
    assert "last30days" in research and "launch writing" in research
    assert call(host, installed, second, "state")["total"] == 0
    second_draft = call(host, installed, second, "draft_create")["draft"]
    call(host, installed, second, "draft_update", id=second_draft["id"], field="text", value="Project B only")
    call(host, installed, second, "brand_update", field="name", value="Project B")
    wrong_project = host.endpoint(host.service, request(host, installed, second, "draft_get", {"id": draft["id"]}))
    assert wrong_project["is_error"] and "no longer exists" in wrong_project["content"], wrong_project
    host.runtime._publish_tools()
    assert host.runtime.available_tools() == []
    assert host.manager.cwd == str(host.selected) == host.service.core.cwd
    # Also check hiding in a project where the plugin itself is enabled.
    host.manager.set_cwd(str(first))
    host.runtime._publish_tools()
    assert host.runtime.available_tools() == []
    host.manager.set_cwd(str(host.selected))

    # Restart the actual child process and manager against the same isolated state.
    host.runtime.close()
    host.manager = host.extensions.ExtensionManager(str(host.selected), root=host.root / "extensions")
    host.runtime = host.runtime_class(host.manager)
    host.service.core.extensions, host.service.core.mcp = host.manager, host.runtime
    assert call(host, installed, first, "draft_get", id=draft["id"])["draft"] == draft
    assert call(host, installed, first, "state")["brand"]["name"] == "Project A"
    assert call(host, installed, second, "state")["brand"]["name"] == "Project B"
    data = {path: path.read_bytes() for path in host.manager.plugin_data_root.glob("*/projects/*.json")}
    assert len(data) == 2 and all(path.is_relative_to(host.root) for path in data)

    host.manager.set_plugin_enabled(installed["id"], False, scope="workspace", workspace=str(first))
    assert_refused(host, request(host, installed, first, "state"), 409)
    assert call(host, installed, second, "state")["total"] == 1
    host.manager.uninstall_plugin(installed["id"])
    host.runtime.refresh()
    assert_refused(host, request(host, installed, second, "state"), 422)
    assert not Path(installed["root"]).exists()
    assert {path: path.read_bytes() for path in data} == data
    inspection = host.manager.inspect_catalog_plugin(marketplace["id"], "social-studio")
    reinstalled = host.manager.install_plugin(
        marketplace["id"], "social-studio", expected_digest=inspection["digest"],
        scope="workspace", workspace=str(first),
    )
    assert call(host, reinstalled, first, "draft_get", id=draft["id"])["draft"] == draft


def test_real_package_update_and_rollback_preserve_drafts(host):
    installed, marketplace, package = install(host)
    project = host.projects[0]
    draft = call(host, installed, project, "draft_create")["draft"]
    draft = call(host, installed, project, "draft_update", id=draft["id"], field="text", value="Keep this draft")["draft"]
    manifest = package / "plugin.json"
    changed = json.loads(manifest.read_text())
    changed["version"] = "0.2.1"
    changed["description"] += " Integration update fixture."
    manifest.write_text(json.dumps(changed))
    reviewed = host.manager.inspect_catalog_plugin(marketplace["id"], "social-studio")
    assert reviewed["digest"] != installed["digest"]
    with pytest.raises(host.extensions.ExtensionError, match="changed after trust review"):
        host.manager.update_plugin(installed["id"], expected_digest=installed["digest"])
    updated = host.manager.update_plugin(installed["id"], expected_digest=reviewed["digest"])
    assert updated["version"] == "0.2.1" and updated["previous_versions"] == ["0.2.0"]
    assert_refused(host, request(host, installed, project, "state"), 409)
    assert call(host, updated, project, "draft_get", id=draft["id"])["draft"] == draft
    rolled_back = host.manager.rollback_plugin(installed["id"])
    assert rolled_back["version"] == "0.2.0" and rolled_back["digest"] == installed["digest"]
    assert_refused(host, request(host, updated, project, "state"), 409)
    assert call(host, rolled_back, project, "draft_get", id=draft["id"])["draft"] == draft
