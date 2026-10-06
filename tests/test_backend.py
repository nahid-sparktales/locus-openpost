import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

from openpost_plugin.core import HTTPClient, MAX_RESPONSE, Studio, StudioError, date_string, json_bytes, validated_origin


class Credentials:
    def __init__(self):
        self.values = {}

    def save(self, key, token):
        self.values[key] = token

    def load(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


class Native:
    def __init__(self, root):
        self.root = root
        self.copied = self.opened = None
        self.prompts = 0

    def prompt_token(self, cancelled):
        self.prompts += 1
        return "fixture-secret"

    def copy(self, text):
        self.copied = text

    def export_path(self, cancelled):
        return str(self.root / "export.json")

    def open(self, url):
        self.opened = url


class Network:
    def __init__(self):
        self.calls = []
        self.routes = {}
        self.routes["GET", "workspaces"] = [{"id": "ws1", "name": "Brand", "can_edit": True}, {"id": "ws2", "name": "Other", "can_edit": True}]
        self.routes["GET", "accounts"] = [{"id": "x1", "platform": "x", "account_username": "team", "is_active": True}]
        self.routes["GET", "publications"] = []

    def __call__(self, origin, token, cancelled):
        network = self

        class Client:
            def request(self, path, **kwargs):
                if cancelled.is_set():
                    raise StudioError("cancelled")
                network.calls.append(("/".join(path), kwargs, origin, token))
                result = network.routes.get((kwargs.get("method", "GET"), "/".join(path)))
                if isinstance(result, Exception):
                    raise result
                if callable(result):
                    result = result(cancelled)
                if result is None:
                    raise AssertionError("Unconfigured request " + "/".join(path))
                return copy.deepcopy(result)
        return Client()


def publication(revision=3, status="draft"):
    return {"id": "pub1", "workspace_id": "ws1", "title": "Release", "source_text": "Useful things", "revision": revision, "status": status, "scheduled_at": "2099-01-01T12:00:00Z", "renditions": [{"id": "r1", "social_account_id": "x1", "platform": "x", "body": "Platform text", "status": "draft", "external_url": ""}]}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.credentials = Credentials()
        self.native = Native(self.root)
        self.network = Network()
        self.studio = self.reopen()
        self.meta = {"com.locus/panel": {"version": 1, "workspace": str(self.root / "project"), "pluginId": "locus-openpost/social-studio", "panelId": "social-studio", "digest": "digest-1"}}

    def reopen(self):
        return Studio(self.root / "data", credentials=self.credentials, platform=self.native, client_factory=self.network, legacy_root=self.root / "legacy")

    def call(self, operation, **payload):
        return self.studio.call(operation, payload, self.meta)

    def create(self, text="Original"):
        draft = self.call("draft_create")["draft"]
        return self.call("draft_update", id=draft["id"], field="text", value=text)["draft"]

    def connect(self, workspace="ws1"):
        self.call("connect", origin="https://openpost.example")
        self.call("connect_workspace", workspaceId=workspace)
        self.network.calls.clear()

    def document_path(self):
        return next((self.root / "data/projects").glob("*.json"))

    def test_native_context_required_and_cannot_be_forged_in_payload(self):
        for meta in ({}, {"com.locus/panel": {}}, {"com.locus/panel": self.meta["com.locus/panel"] | {"pluginId": "other"}}, {"com.locus/panel": self.meta["com.locus/panel"] | {"workspace": "relative"}}):
            with self.assertRaises(StudioError):
                self.studio.call("state", {"workspace": "/a", **self.meta}, meta)
        self.assertFalse((self.root / "data").exists())

    def test_drafts_brand_persist_and_project_isolation_uses_canonical_path(self):
        draft = self.create()
        self.call("draft_update", id=draft["id"], field="variant:x", value="Short")
        self.call("draft_update", id=draft["id"], field="plannedAt", value="2028-01-01T00:00:00-05:00")
        self.call("brand_update", field="name", value="Studio")
        self.studio = self.reopen()
        state = self.call("state")
        self.assertEqual(state["brand"]["name"], "Studio")
        full = self.call("draft_get", id=draft["id"])["draft"]
        self.assertEqual(full["variants"], {"x": "Short"})
        self.assertEqual(full["plannedAt"], "2028-01-01T05:00:00Z")
        other = copy.deepcopy(self.meta)
        other["com.locus/panel"]["workspace"] += "/other"
        self.assertEqual(self.studio.call("state", {}, other)["total"], 0)
        self.assertEqual(self.network.calls, [])
        (self.root / "project").mkdir()
        (self.root / "alias").symlink_to(self.root / "project")
        other["com.locus/panel"]["workspace"] = str(self.root / "alias")
        self.assertEqual(self.studio.call("state", {}, other)["total"], 1)

    def test_invalid_fields_and_limits_are_rejected_without_data_loss(self):
        draft = self.create()
        for field, value in (("text", "a" * 50001), ("title", "a" * 201), ("channels", ["x", "x"]), ("channels", ["unknown"]), ("plannedAt", "2025-01-01"), ("handoff", {}), ("id", "new")):
            with self.assertRaises(StudioError):
                self.call("draft_update", id=draft["id"], field=field, value=value)
        self.assertEqual(self.call("draft_get", id=draft["id"])["draft"]["text"], "Original")

    def test_corrupt_store_is_never_overwritten(self):
        self.create()
        path = self.document_path()
        path.write_bytes(b"not json")
        self.studio = self.reopen()
        for operation in ("state", "draft_create", "connect"):
            with self.assertRaisesRegex(StudioError, "original file has been preserved"):
                self.call(operation)
        self.assertEqual(path.read_bytes(), b"not json")
        self.assertEqual(self.native.prompts, 0)

    def test_origin_validation(self):
        for value in ("http://example.com", "https://u:p@example.com", "https://example.com/api/v1", "https://example.com?token=x", "file:///tmp/a", "https://example.com#fragment", "https://example.com:abc", "https://exam ple.com"):
            with self.assertRaises(StudioError):
                validated_origin(value)
        self.assertEqual(validated_origin(" http://localhost:8080/ "), "http://localhost:8080")
        self.assertEqual(validated_origin("http://[::1]:8000"), "http://[::1]:8000")

    def test_connection_secret_never_enters_document_or_results(self):
        self.connect()
        self.assertNotIn("fixture-secret", self.document_path().read_text())
        self.assertNotIn("fixture-secret", json.dumps(self.call("state")))
        self.assertNotIn("credentialID", json.dumps(self.call("state")))
        self.assertEqual(list(self.credentials.values.values()), ["fixture-secret"])
        self.call("disconnect")
        self.assertEqual(self.credentials.values, {})

    def test_connection_staging_bound_to_panel_and_expires(self):
        self.call("connect", origin="https://openpost.example")
        alternate = copy.deepcopy(self.meta)
        alternate["com.locus/panel"]["panelId"] = "another-panel"
        with self.assertRaises(StudioError):
            self.studio.call("connect_workspace", {"workspaceId": "ws1"}, alternate)
        with patch("openpost_plugin.core.time.monotonic", return_value=10**12):
            with self.assertRaises(StudioError):
                self.call("connect_workspace", workspaceId="ws1")
        self.assertEqual(self.credentials.values, {})

    def test_transfer_creates_draft_and_uses_platform_variant(self):
        self.connect()
        draft = self.create()
        self.call("draft_update", id=draft["id"], field="variant:x", value="Short")
        self.network.routes["POST", "publications"] = publication()
        result = self.call("send_draft", id=draft["id"], accountIds=["x1"])
        requests = self.network.calls
        body = json.loads(requests[-1][1]["body"])
        self.assertEqual(body["renditions"], [{"social_account_id": "x1", "body": "Short"}])
        self.assertEqual(body["content_profile"], "short_text")
        self.assertNotIn("status", body)
        self.assertEqual([x[0] for x in requests], ["accounts", "publications"])
        self.assertNotIn("body", result["draft"]["handoff"])
        self.assertNotIn("key", result["draft"]["handoff"])

    def test_transfer_retries_exact_envelope_after_restart(self):
        self.connect()
        draft = self.create("Do not change this")
        self.network.routes["POST", "publications"] = StudioError("Outcome unknown")
        with self.assertRaises(StudioError):
            self.call("send_draft", id=draft["id"], accountIds=[])
        original_request = copy.deepcopy(self.network.calls[-1])
        with self.assertRaisesRegex(StudioError, "Duplicate"):
            self.call("draft_update", id=draft["id"], field="text", value="changed")
        self.studio = self.reopen()
        self.network.routes["POST", "publications"] = publication()
        self.call("send_draft", id=draft["id"], accountIds=["irrelevant-on-retry"])
        self.assertEqual(self.network.calls[-1], original_request)
        self.assertEqual(self.call("draft_get", id=draft["id"])["draft"]["handoff"]["publicationID"], "pub1")
        count = len(self.network.calls)
        self.call("send_draft", id=draft["id"])
        self.assertEqual(len(self.network.calls), count)
        self.assertNotIn("publish-now", [c[0] for c in self.network.calls])

    def test_transfer_cannot_cross_remote_workspace(self):
        self.connect()
        draft = self.create()
        self.network.routes["POST", "publications"] = StudioError("failed")
        with self.assertRaises(StudioError):
            self.call("send_draft", id=draft["id"])
        self.connect("ws2")
        with self.assertRaisesRegex(StudioError, "another OpenPost workspace"):
            self.call("send_draft", id=draft["id"])
        self.assertEqual(self.network.calls, [])

    def test_duplicate_unlocks_transfer_without_reusing_schedule_or_key(self):
        self.connect()
        draft = self.create()
        self.call("draft_update", id=draft["id"], field="plannedAt", value="2099-01-01T12:00:00Z")
        self.network.routes["POST", "publications"] = publication()
        self.call("send_draft", id=draft["id"])
        duplicate = self.call("draft_duplicate", id=draft["id"])["draft"]
        self.assertNotEqual(duplicate["id"], draft["id"])
        self.assertIsNone(duplicate["handoff"])
        self.assertIsNone(duplicate["plannedAt"])

    def review(self):
        self.network.routes["GET", "publications/pub1"] = publication()
        return self.call("publication_get", id="pub1")

    def test_actions_require_review_by_this_panel_and_same_connection(self):
        self.connect()
        with self.assertRaisesRegex(StudioError, "review"):
            self.call("publication_action", id="pub1", revision=3, action="publish-now")
        self.review()
        alternate = copy.deepcopy(self.meta)
        alternate["com.locus/panel"]["digest"] = "other-digest"
        with self.assertRaises(StudioError):
            self.studio.call("publication_action", {"id": "pub1", "revision": 3, "action": "publish-now"}, alternate)
        self.connect()
        with self.assertRaises(StudioError):
            self.call("publication_action", id="pub1", revision=3, action="publish-now")

    def test_failed_validation_prevents_publication(self):
        self.connect()
        self.review()
        self.network.calls.clear()
        self.network.routes["POST", "publications/pub1/validate"] = {"valid": False, "issues": [{"message": "fixture-secret", "severity": "error"}]}
        with self.assertRaisesRegex(StudioError, "validation failed") as error:
            self.call("publication_action", id="pub1", revision=3, action="publish-now")
        self.assertNotIn("fixture-secret", str(error.exception))
        self.assertEqual([x[0] for x in self.network.calls], ["publications/pub1/validate"])

    def test_conflict_never_silently_approves_a_new_revision(self):
        self.connect()
        self.review()
        self.network.calls.clear()
        self.network.routes["POST", "publications/pub1/validate"] = {"valid": True}
        self.network.routes["POST", "publications/pub1/publish-now"] = StudioError("The publication changed")
        with self.assertRaisesRegex(StudioError, "changed"):
            self.call("publication_action", id="pub1", revision=3, action="publish-now")
        self.assertEqual([x[0] for x in self.network.calls], ["publications/pub1/validate", "publications/pub1/publish-now"])
        self.assertEqual(json.loads(self.network.calls[-1][1]["body"]), {"expected_revision": 3})
        self.assertEqual(self.network.calls[-1][1]["key"], "locus-pub1-3-publish-now")

    def test_accepted_publication_not_marked_delivered(self):
        self.connect()
        self.review()
        self.network.routes["POST", "publications/pub1/validate"] = {"valid": True}
        self.network.routes["POST", "publications/pub1/publish-now"] = {"message": "Queued", "job_id": "job1"}
        self.network.routes["GET", "publications/pub1"] = publication(4, "publishing")
        result = self.call("publication_action", id="pub1", revision=3, action="publish-now")
        self.assertEqual(result["publication"]["status"], "publishing")
        self.assertIn("accepted", result["notice"])
        self.assertNotIn("success", result["notice"])

    def test_cancel_does_not_run_validation(self):
        self.connect()
        self.review()
        self.network.calls.clear()
        self.network.routes["POST", "publications/pub1/cancel"] = {"message": "accepted"}
        self.call("publication_action", id="pub1", revision=3, action="cancel")
        self.assertEqual(self.network.calls[0][0], "publications/pub1/cancel")

    def test_schedule_requires_future_date_and_passes_reviewed_revision(self):
        self.connect()
        self.network.routes["GET", "publications/pub1"] = publication() | {"scheduled_at": "2000-01-01T00:00:00Z"}
        self.call("publication_get", id="pub1")
        with self.assertRaisesRegex(StudioError, "future schedule"):
            self.call("publication_action", id="pub1", revision=3, action="schedule")
        self.review()
        self.network.routes["POST", "publications/pub1/validate"] = {"valid": True}
        self.network.routes["POST", "publications/pub1/schedule"] = {"message": "accepted"}
        self.call("publication_action", id="pub1", revision=3, action="schedule")
        self.assertEqual(json.loads(self.network.calls[-2][1]["body"]), {"expected_revision": 3})

    def test_cancelled_transfer_leaves_saved_envelope_for_explicit_retry(self):
        self.connect()
        draft = self.create()
        cancelled = threading.Event()
        def uncertain(flag):
            flag.set()
            return publication()
        self.network.routes["POST", "publications"] = uncertain
        with self.assertRaisesRegex(StudioError, "cancelled"):
            self.studio.call("send_draft", {"id": draft["id"]}, self.meta, cancelled)
        handoff = json.loads(self.document_path().read_text())["drafts"][0]["handoff"]
        self.assertNotIn("publicationID", handoff)
        self.assertEqual(len(self.network.calls), 1)

    def test_cancelled_validation_never_sends_publish(self):
        self.connect()
        self.review()
        self.network.calls.clear()
        def validation(flag):
            flag.set()
            return {"valid": True}
        self.network.routes["POST", "publications/pub1/validate"] = validation
        with self.assertRaises(StudioError):
            self.call("publication_action", id="pub1", revision=3, action="publish-now")
        self.assertEqual([c[0] for c in self.network.calls], ["publications/pub1/validate"])

    def test_legacy_import_preserves_identifiers_dates_and_exact_envelope(self):
        original_id = "39F09E58-B0D6-4948-B60D-382FC4C692E2"
        body = b'{ "workspace_id": "ws1", "source_text": "Original", "renditions": [] }'
        legacy = {"version": 1, "brand": {"name": "Locus", "audience": "Builders", "voice": "Plain", "topics": "Tools"}, "connection": {"origin": "https://openpost.example", "workspaceID": "ws1", "workspaceName": "Brand", "credentialID": "legacy-secret-id"}, "drafts": [{"id": original_id, "title": "Hello", "text": "Original", "channels": ["x"], "variants": {"x": "Short"}, "updatedAt": 0, "plannedAt": 800_000_000.125, "handoff": {"origin": "https://openpost.example", "workspaceID": "ws1", "key": "original-key", "body": base64.b64encode(body).decode()}}]}
        legacy["drafts"][0]["handoff"]["body"] = base64.b64encode(body).decode()
        digest = hashlib.sha256(self.meta["com.locus/panel"]["workspace"].encode()).hexdigest()
        source = self.root / "legacy/Locus/Social Studio" / (digest + ".json")
        source.parent.mkdir(parents=True)
        source.write_bytes(json_bytes(legacy))
        original_bytes = source.read_bytes()
        self.credentials.values["legacy-secret-id"] = "untouched"
        self.assertEqual(self.call("state")["legacySources"], ["Locus"])
        self.assertEqual(self.call("import_legacy", edition="Locus")["imported"], 1)
        saved = json.loads(self.document_path().read_text())
        self.assertEqual(saved["drafts"][0]["id"], original_id)
        self.assertEqual(saved["drafts"][0]["updatedAt"], "2001-01-01T00:00:00Z")
        self.assertEqual(saved["drafts"][0]["plannedAt"], date_string(800_000_000.125, True))
        self.assertEqual(saved["drafts"][0]["handoff"], legacy["drafts"][0]["handoff"])
        self.assertIsNone(saved["connection"])
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(self.credentials.values["legacy-secret-id"], "untouched")
        self.connect()
        self.network.routes["POST", "publications"] = publication()
        self.call("send_draft", id=original_id)
        self.assertEqual(self.network.calls[-1][1]["body"], body)
        self.assertEqual(self.network.calls[-1][1]["key"], "original-key")

    def test_import_does_not_overwrite_existing_content(self):
        self.create()
        with self.assertRaisesRegex(StudioError, "empty"):
            self.call("import_legacy", edition="Locus")
        self.assertEqual(self.call("state")["total"], 1)

    def test_export_strips_connection_and_transfer_envelopes(self):
        self.connect()
        draft = self.create()
        self.network.routes["POST", "publications"] = publication()
        self.call("send_draft", id=draft["id"])
        self.call("export")
        exported = json.loads((self.root / "export.json").read_text())
        self.assertIsNone(exported["connection"])
        self.assertNotIn("handoff", exported["drafts"][0])
        self.assertNotIn("fixture-secret", json.dumps(exported))

    def test_copy_variant_remove_variant_and_assistant_prompts(self):
        draft = self.create()
        self.call("draft_update", id=draft["id"], field="variant:x", value="Short")
        self.call("copy", id=draft["id"], channel="x")
        self.assertEqual(self.native.copied, "Short")
        self.call("draft_update", id=draft["id"], field="variant:x", value=None)
        self.call("copy", id=draft["id"], channel="x")
        self.assertEqual(self.native.copied, "Original")
        self.call("brand_update", field="audience", value="Builders")
        prompt = self.call("assistant_prompt", action="research", topic="Local AI")["prompt"]
        self.assertIn("last30days skill", prompt)
        self.assertIn("Builders", prompt)
        self.assertIn("Do not schedule or publish", prompt)
        self.assertIn("Original", self.call("assistant_prompt", action="adapt", id=draft["id"])["prompt"])

    def test_draft_order_follows_latest_edit_and_planned_count_excludes_handoffs(self):
        with patch("openpost_plugin.core.now", return_value="2026-01-01T00:00:00Z"):
            first = self.create("First")
        with patch("openpost_plugin.core.now", return_value="2026-01-01T00:00:00.500000Z"):
            second = self.create("Second")
        self.assertEqual([d["id"] for d in self.call("state")["drafts"]], [second["id"], first["id"]])
        self.call("draft_update", id=first["id"], field="plannedAt", value="2099-01-01T00:00:00Z")
        state = self.call("state")
        self.assertEqual(state["drafts"][0]["id"], first["id"])
        self.assertEqual(state["counts"]["planned"], 1)
        self.connect()
        self.network.routes["POST", "publications"] = StudioError("Outcome unknown")
        with self.assertRaises(StudioError):
            self.call("send_draft", id=first["id"])
        self.assertEqual(self.call("state")["counts"]["planned"], 0)
        self.assertEqual(self.call("state")["counts"]["pendingTransfers"], 1)

    def test_pagination_search_and_summaries_do_not_include_full_drafts(self):
        first = self.create("a" * 1000 + "unique")
        self.create("second")
        page = self.call("state", limit=1)
        self.assertEqual(len(page["drafts"]), 1)
        self.assertEqual(page["total"], 2)
        search = self.call("state", search="unique")
        self.assertEqual(search["total"], 1)
        self.assertEqual(search["drafts"][0]["id"], first["id"])
        self.assertLessEqual(len(search["drafts"][0]["text"]), 240)
        self.assertNotIn("variants", search["drafts"][0])
        self.assertEqual(search["counts"]["drafts"], 2)

    def test_refresh_preserves_actual_destination_delivery_results(self):
        self.connect()
        post = publication(status="partial")
        post["renditions"][0].update(status="failed", error_message="Provider refused", delivery={"state": "failed", "recovery_action": "retry"})
        self.network.routes["GET", "publications"] = [post]
        result = self.call("refresh", limit=1, offset=20)
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["publications"][0]["renditions"][0]["delivery"]["state"], "failed")
        self.assertEqual(result["accounts"][0]["account_username"], "team")
        self.assertEqual(self.network.calls[-1][1]["query"], {"workspace_id": "ws1", "limit": 1, "offset": 20})

    def test_response_overflow_is_an_explicit_error_not_truncated_json(self):
        self.connect()
        self.network.routes["GET", "publications/pub1"] = publication() | {"source_text": "x" * MAX_RESPONSE}
        with self.assertRaisesRegex(StudioError, "panel limit"):
            self.call("publication_get", id="pub1")

    def test_large_variants_remain_editable_via_individual_field_reads(self):
        draft = self.create('"' * 50_000)
        from openpost_plugin.core import CHANNELS
        for channel in CHANNELS:
            result = self.call("draft_update", id=draft["id"], field="variant:" + channel, value='"' * 50_000)
        self.assertTrue(result["draft"]["variantsOmitted"])
        self.assertEqual(set(result["draft"]["variantChannels"]), set(CHANNELS))
        self.assertLess(len(json_bytes(result).decode()), MAX_RESPONSE)
        full = self.call("draft_get", id=draft["id"], field="variant:x")
        self.assertEqual(full["value"], '"' * 50_000)

    def test_overflow_publication_does_not_authorize_unseen_revision(self):
        self.connect()
        self.network.routes["GET", "publications/pub1"] = publication() | {"source_text": "x" * MAX_RESPONSE}
        with self.assertRaises(StudioError):
            self.call("publication_get", id="pub1")
        self.network.calls.clear()
        with self.assertRaisesRegex(StudioError, "review"):
            self.call("publication_action", id="pub1", revision=3, action="publish-now")
        self.assertEqual(self.network.calls, [])

    def test_concurrent_processes_cannot_overwrite_project_data(self):
        alternate = self.reopen()
        panel = self.studio.panel_context(self.meta)
        with self.studio.transaction(panel, threading.Event()):
            with self.assertRaisesRegex(StudioError, "Another Social Studio"):
                alternate.call("draft_create", {}, self.meta)
        self.assertEqual(self.call("state")["total"], 0)

    def test_external_links_must_be_https_and_from_current_workspace(self):
        self.connect()
        post = publication()
        post["renditions"][0]["external_url"] = "file:///tmp/secret"
        self.network.routes["GET", "publications/pub1"] = post
        with self.assertRaises(StudioError):
            self.call("open", target="rendition", id="pub1", renditionId="r1")
        post["renditions"][0]["external_url"] = "https://social.example/post/1"
        self.call("open", target="rendition", id="pub1", renditionId="r1")
        self.assertEqual(self.native.opened, "https://social.example/post/1")
        post["workspace_id"] = "ws2"
        with self.assertRaises(StudioError):
            self.call("publication_get", id="pub1")


class HTTPTests(unittest.TestCase):
    def test_error_body_does_not_leak_credentials(self):
        for code in (301, 401, 403, 409, 422, 429, 500):
            error = urllib.error.HTTPError("https://example.com", code, "fixture-secret", {}, None)
            with patch("urllib.request.OpenerDirector.open", side_effect=error):
                with self.assertRaises(StudioError) as raised:
                    HTTPClient("https://example.com", "fixture-secret", threading.Event()).request(["workspaces"])
                self.assertNotIn("fixture-secret", str(raised.exception))

    def test_response_redacts_accidentally_reflected_token(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"name":"fixture-secret"}'
        with patch("urllib.request.OpenerDirector.open", return_value=response):
            result = HTTPClient("https://example.com", "fixture-secret", threading.Event()).request(["workspaces"])
        self.assertEqual(result, {"name": "[redacted]"})

    def test_actual_http_redirect_is_not_followed(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        paths = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/credential-sink")
                self.end_headers()
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = HTTPClient(f"http://127.0.0.1:{server.server_port}", "fixture-secret", threading.Event())
            with self.assertRaisesRegex(StudioError, "redirected"):
                client.request(["workspaces"])
            self.assertEqual(paths, ["/api/v1/workspaces"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_identifiers_cannot_escape_api_path(self):
        client = HTTPClient("https://example.com", "fixture-secret", threading.Event())
        for identifier in ("..", "%2f", "a/b", "a\\b", "id\n"):
            with self.assertRaises(StudioError):
                client.request(["publications", identifier])


if __name__ == "__main__":
    unittest.main()
