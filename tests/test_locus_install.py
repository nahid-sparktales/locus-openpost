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


def test_bundled_native_plugin_upgrades_to_external_package_with_legacy_import(host):
    """An unsupported installed screen must remain discoverable and updatable.

    The manifest fixture is the original Locus bundled plugin at 4cce6ebf.
    Seed its on-disk installation because the new host correctly refuses to
    install the removed native capability as a new plugin.
    """
    import base64
    import hashlib

    marketplace = host.root / "legacy-marketplace"
    catalog = marketplace / ".agents/plugins/marketplace.json"
    catalog.parent.mkdir(parents=True)
    old_source = marketplace / "bundled/social-studio"
    (old_source / ".codex-plugin").mkdir(parents=True)
    (old_source / "ui").mkdir()
    (old_source / "assets").mkdir()
    shutil.copyfile(PACKAGE / "tests/fixtures/legacy-plugin-0.1.1.json", old_source / ".codex-plugin/plugin.json")
    shutil.copyfile(PACKAGE / "assets/icon.svg", old_source / "assets/icon.svg")
    (old_source / "ui/index.html").write_text("<!doctype html><title>Social Studio native screen</title>")
    legacy_source = {"source": "local", "path": "./bundled/social-studio"}

    def write_catalog(source):
        catalog.write_text(json.dumps({"name": "Legacy integration test", "plugins": [{
            "name": "social-studio", "source": source,
        }]}))

    write_catalog(legacy_source)
    registered = host.manager.add_marketplace(str(marketplace))
    plugin_id = f"{registered['id']}/social-studio"
    old_root = host.manager.plugins_root / registered["id"] / "social-studio/0.1.1"
    shutil.copytree(old_source, old_root)
    old_digest = host.extensions._tree_digest(old_root)
    first, second = host.projects
    scopes = {"enabled_global": False, "enabled_workspaces": [str(first.resolve())],
              "disabled_workspaces": [str(second.resolve())]}
    legacy_record = {
        "id": plugin_id, "marketplace_id": registered["id"], "name": "social-studio",
        "version": "0.1.1", "root": str(old_root), "digest": old_digest,
        "source": legacy_source, "previous": [], "installed_at": "2026-01-01T00:00:00Z",
        **scopes,
    }
    host.manager._state["plugins"].append(legacy_record)
    host.manager._save()
    old_view = next(p for p in host.manager.snapshot()["plugins"] if p["id"] == plugin_id)
    assert old_view["version"] == "0.1.1" and old_view["error"]
    assert old_view["screens"] == [] and old_view["panels"] == []
    assert "unsupported" in old_view["error"]

    data_root = host.manager.plugin_data_root / host.extensions._slug(plugin_id)
    data_root.mkdir(parents=True)
    sentinel = data_root / "existing-plugin-data.json"
    sentinel.write_text('{"keep":"existing plugin data"}')
    existing_data = sentinel.read_bytes()
    native_body = b'{ "workspace_id": "ws_original", "source_text": "Keep exact bytes", "renditions": [] }'
    legacy_document = {
        "version": 1,
        "brand": {"name": "Earlier brand", "audience": "Builders", "voice": "Direct", "topics": "Shipping"},
        "connection": {"origin": "https://openpost.example", "workspaceID": "ws_original",
                       "workspaceName": "Earlier workspace", "credentialID": "social-studio.original-keychain-item"},
        "drafts": [{"id": "39F09E58-B0D6-4948-B60D-382FC4C692E2", "title": "Original post",
                    "text": "Keep exact bytes", "channels": ["x"], "variants": {"x": "Short version"},
                    "updatedAt": 0, "plannedAt": 800000000.125,
                    "handoff": {"origin": "https://openpost.example", "workspaceID": "ws_original",
                                "key": "locus-draft-original-transfer", "body": base64.b64encode(native_body).decode()}}],
    }
    project_hash = hashlib.sha256(str(first.resolve()).encode()).hexdigest()
    original_files = {}
    for edition in ("Locus", "LocusX"):
        path = host.home / "Library/Application Support" / edition / "Social Studio" / f"{project_hash}.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(legacy_document))
        original_files[path] = path.read_bytes()
    original_cache = {path: path.read_bytes() for path in old_root.rglob("*") if path.is_file()}

    # Keep the same marketplace/plugin identity while moving its catalog source
    # from bundled files to the separately distributed repository.
    remote = os.environ.get("LOCUS_PLUGIN_SOURCE", "")
    if remote:
        parsed = urlparse(remote)
        assert parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
        external_source = {"source": "url", "url": remote}
    else:
        clean_package(marketplace / "external/social-studio")
        external_source = {"source": "local", "path": "./external/social-studio"}
    write_catalog(external_source)
    host.manager.refresh_marketplace(registered["id"])
    browse = next(p for p in host.manager.catalog(marketplace_id=registered["id"]) if p["name"] == "social-studio")
    assert browse["id"] == plugin_id and browse["installed"] and browse["available"]
    assert browse["installed_version"] == "0.1.1" and browse["source"] == external_source
    reviewed = host.manager.inspect_catalog_plugin(registered["id"], "social-studio")
    assert reviewed["plugin"]["version"] == "0.2.0"
    assert reviewed["capability_diff"]["kind"] == "update"
    assert reviewed["capability_diff"]["requires_renewed_trust"] is True
    assert "can no longer be inspected" in " ".join(reviewed["capability_diff"]["changes"])
    assert reviewed["trust"]["panels"][0]["tools"] == ["social_studio"]
    with pytest.raises(host.extensions.ExtensionError, match="changed after trust review"):
        host.manager.update_plugin(plugin_id, expected_digest=old_digest)
    updated = host.manager.update_plugin(plugin_id, expected_digest=reviewed["digest"])
    assert updated["id"] == plugin_id and updated["version"] == "0.2.0"
    assert updated["previous_versions"] == ["0.1.1"] and updated["error"] is None
    assert {key: updated[key] for key in scopes} == scopes
    assert sentinel.read_bytes() == existing_data
    assert {path: path.read_bytes() for path in original_cache} == original_cache
    assert {path: path.read_bytes() for path in original_files} == original_files
    assert_refused(host, request(host, updated, second, "state"), 409)

    state = call(host, updated, first, "state")
    assert state["legacySources"] == ["Locus", "LocusX"] and state["connection"] is None
    assert call(host, updated, first, "import_legacy", edition="Locus")["imported"] == 1
    migrated = call(host, updated, first, "draft_get", id=legacy_document["drafts"][0]["id"])["draft"]
    assert migrated["id"] == legacy_document["drafts"][0]["id"]
    assert migrated["variants"] == {"x": "Short version"} and migrated["updatedAt"] == "2001-01-01T00:00:00Z"
    assert call(host, updated, first, "state")["brand"] == legacy_document["brand"]
    saved = json.loads((data_root / "projects" / f"{project_hash}.json").read_text())
    assert saved["connection"] is None, "An upgraded plugin must require a fresh native connection"
    assert saved["drafts"][0]["handoff"] == legacy_document["drafts"][0]["handoff"]
    assert base64.b64decode(saved["drafts"][0]["handoff"]["body"]) == native_body
    assert {path: path.read_bytes() for path in original_files} == original_files
    assert sentinel.read_bytes() == existing_data
