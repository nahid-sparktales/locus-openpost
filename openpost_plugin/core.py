"""Local drafts and OpenPost HTTP operations. Only the native panel supplies identity."""
import base64
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager

from .platform import Keychain, MacOS, PlatformError

CHANNELS = {"linkedin": "LinkedIn", "x": "X", "bluesky": "Bluesky", "threads": "Threads", "mastodon": "Mastodon", "instagram": "Instagram", "facebook": "Facebook", "tiktok": "TikTok", "youtube": "YouTube", "pinterest": "Pinterest"}
MAX_DOCUMENT = 16 * 1024 * 1024
MAX_RESPONSE = 900_000
APPLE_EPOCH = 978307200


class StudioError(Exception):
    """Safe, actionable text; never wrap provider bodies or transport exceptions."""


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def date_string(value, legacy=False):
    if value is None:
        return None
    try:
        if legacy and isinstance(value, (float, int)) and not isinstance(value, bool):
            date = dt.datetime.fromtimestamp(value + APPLE_EPOCH, dt.timezone.utc)
        else:
            date = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError()
        return date.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        raise StudioError("Choose a valid date with a timezone.") from None


def text_value(value, limit=50_000):
    if not isinstance(value, str) or len(value) > limit:
        raise StudioError(f"Keep this field under {limit:,} characters.")
    return value


def resource_id(value):
    if not isinstance(value, str) or not value or len(value) > 200 or any(c in value for c in "/\\%\r\n") or value in (".", ".."):
        raise StudioError("OpenPost returned an invalid resource identifier.")
    return value


def validated_origin(raw):
    try:
        raw = raw.strip()
        parsed = urllib.parse.urlsplit(raw)
        allowed_scheme = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))
        if not allowed_scheme or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in ("", "/") or parsed.query or parsed.fragment or any(c.isspace() for c in raw):
            raise ValueError()
        _ = parsed.port
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    except (ValueError, AttributeError, TypeError):
        raise StudioError("Enter an HTTPS origin, such as https://app.openpo.st. Localhost may use HTTP. Leave off /api/v1.") from None


def redact_secret(value, token):
    if isinstance(value, str):
        return value.replace(token, "[redacted]")
    if isinstance(value, list):
        return [redact_secret(item, token) for item in value]
    if isinstance(value, dict):
        return {redact_secret(key, token): redact_secret(item, token) for key, item in value.items()}
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HTTPClient:
    def __init__(self, origin, token, cancelled):
        self.origin = validated_origin(origin)
        if not isinstance(token, str) or not token.strip() or any(c in token for c in "\r\n"):
            raise StudioError("Connect OpenPost in Accounts first.")
        self.token = token
        self.cancelled = cancelled

    def request(self, path, *, query=None, method="GET", body=None, key=None):
        check_cancelled(self.cancelled)
        url = self.origin + "/api/v1/" + "/".join(urllib.parse.quote(resource_id(p), safe="") for p in path)
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if key:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            # No retries and no redirects: uncertain writes remain explicit retries.
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
                data = response.read(MAX_DOCUMENT + 1)
            check_cancelled(self.cancelled)
            if len(data) > MAX_DOCUMENT:
                raise StudioError("OpenPost returned too much data. Open this publication in OpenPost.")
            return redact_secret(json.loads(data), self.token)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            details = {401: "The token expired or is invalid. Reconnect in Accounts.", 403: "This token cannot perform that action in this workspace. Check its read/write permissions.", 409: "The publication changed. Refresh Activity and review it before trying again.", 422: "OpenPost rejected the content or schedule. Review the publication in OpenPost.", 429: "OpenPost is rate limiting requests. Try again later."}
            detail = "The server redirected the request. Check the instance's final HTTPS address." if 300 <= code < 400 else details.get(code, f"OpenPost returned HTTP {code}. Try again or check the instance.")
            raise StudioError(detail) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise StudioError("Couldn't reach OpenPost. The outcome of a write may be unknown; retry explicitly using its saved request.") from None
        except (ValueError, UnicodeError):
            raise StudioError("OpenPost returned an unreadable response. Refresh before retrying an action.") from None


def check_cancelled(cancelled):
    if cancelled.is_set():
        raise StudioError("The panel request was cancelled. A request already sent to OpenPost may still complete; refresh before taking another action.")


def empty_document():
    return {"version": 2, "drafts": [], "brand": {"name": "", "audience": "", "voice": "Clear, useful, conversational. Avoid hype.", "topics": ""}, "connection": None}


def validate_document(document, legacy=False):
    if not isinstance(document, dict) or document.get("version") != (1 if legacy else 2):
        raise StudioError("This Social Studio file has an unsupported version.")
    drafts = document.get("drafts")
    if not isinstance(drafts, list) or len(drafts) > 2000:
        raise StudioError("Social Studio supports up to 2,000 local drafts.")
    ids = set()
    for draft in drafts:
        if not isinstance(draft, dict):
            raise StudioError("A saved draft is invalid.")
        identifier = str(uuid.UUID(draft["id"]))
        if identifier in ids:
            raise StudioError("The file contains duplicate draft identifiers.")
        ids.add(identifier)
        text_value(draft["title"], 200)
        text_value(draft["text"])
        channels = draft["channels"]
        if not isinstance(channels, list) or any(c not in CHANNELS for c in channels) or len(channels) != len(set(channels)):
            raise StudioError("A saved draft contains invalid channels.")
        variants = draft["variants"]
        if not isinstance(variants, dict) or any(c not in CHANNELS for c in variants):
            raise StudioError("A saved platform version is invalid.")
        for value in variants.values():
            text_value(value)
        date_string(draft["updatedAt"], legacy)
        date_string(draft.get("plannedAt"), legacy)
        handoff = draft.get("handoff")
        if handoff:
            validated_origin(handoff["origin"])
            resource_id(handoff["workspaceID"])
            resource_id(handoff["key"])
            body = base64.b64decode(handoff["body"], validate=True)
            envelope = json.loads(body)
            if envelope.get("workspace_id") != handoff["workspaceID"]:
                raise StudioError("A saved transfer belongs to another workspace.")
            if handoff.get("publicationID"):
                resource_id(handoff["publicationID"])
    brand = document["brand"]
    for field in ("name", "audience", "voice", "topics"):
        text_value(brand[field])
    connection = document.get("connection")
    if connection:
        validated_origin(connection["origin"])
        resource_id(connection["workspaceID"])
        text_value(connection["workspaceName"], 1000)
        if not legacy and not connection.get("credentialID", "").startswith("social-studio."):
            raise StudioError("The saved connection is invalid. Reconnect OpenPost.")


def read_document(path, legacy=False):
    try:
        if path.stat().st_size > MAX_DOCUMENT:
            raise ValueError()
        data = path.read_bytes()
        document = json.loads(data)
        validate_document(document, legacy)
        return document
    except (OSError, ValueError, TypeError, KeyError, StudioError):
        raise StudioError("Couldn't read your Social Studio data. The original file has been preserved. Restore its data file before making changes.") from None


def write_json(path, value, cancelled):
    check_cancelled(cancelled)
    data = json_bytes(value)
    if len(data) > MAX_DOCUMENT:
        raise StudioError("Social Studio is full. Export and remove some local drafts before adding more.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".social-studio-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        check_cancelled(cancelled)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def public_draft(draft, summary=False):
    result = {k: copy.deepcopy(v) for k, v in draft.items() if k != "handoff"}
    result["plannedAt"] = date_string(draft.get("plannedAt"))
    if summary:
        result["text"] = draft["text"][:240]
        result.pop("variants", None)
    handoff = draft.get("handoff")
    result["handoff"] = {k: handoff.get(k) for k in ("origin", "workspaceID", "publicationID")} if handoff else None
    if not summary and len(json_bytes(result).decode()) > MAX_RESPONSE - 100:
        result["variantChannels"] = list(result["variants"])
        result["variants"] = {}
        result["variantsOmitted"] = True
    return result


def public_connection(connection):
    return {k: connection[k] for k in ("origin", "workspaceID", "workspaceName")} if connection else None


def page_arguments(payload):
    offset, limit = payload.get("offset", 0), payload.get("limit", 50)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise StudioError("Choose a page size between 1 and 100.")
    return offset, limit


def publication_summary(publication):
    return {k: publication.get(k) for k in ("id", "workspace_id", "title", "status", "revision", "scheduled_at", "updated_at")} | {"source_text": str(publication.get("source_text", ""))[:240], "renditions": [{k: r.get(k) for k in ("id", "platform", "status", "error_message", "external_url", "social_account_id", "delivery")} for r in publication.get("renditions") or []]}


class Studio:
    def __init__(self, data_root, *, credentials=None, platform=None, client_factory=HTTPClient, legacy_root=None):
        self.root = Path(data_root).resolve()
        self.credentials = credentials  # Lazily access Keychain; local drafts work independently.
        self.platform = platform or MacOS()
        self.client_factory = client_factory
        self.legacy_root = Path(legacy_root) if legacy_root else Path.home() / "Library/Application Support"
        self.cache = {}
        self.pending_connections = {}
        self.reviews = {}
        self.lock = threading.Lock()

    def keychain(self):
        if self.credentials is None:
            self.credentials = Keychain()
        return self.credentials

    @staticmethod
    def panel_context(metadata):
        panel = metadata.get("com.locus/panel") if isinstance(metadata, dict) else None
        if not isinstance(panel, dict) or panel.get("version") != 1 or not isinstance(panel.get("pluginId"), str) or panel["pluginId"].split("/")[-1] != "social-studio" or panel.get("panelId") != "social-studio" or not all(isinstance(panel.get(k), str) and panel[k] for k in ("workspace", "panelId", "digest")):
            raise StudioError("Open Social Studio from Locus's Work menu. A trusted project panel context is required.")
        workspace = panel["workspace"]
        if not os.path.isabs(workspace) or "\x00" in workspace:
            raise StudioError("The panel project path is invalid.")
        return panel | {"workspace": str(Path(workspace).resolve())}

    @contextmanager
    def transaction(self, panel, cancelled):
        check_cancelled(cancelled)
        if not self.lock.acquire(blocking=False):
            raise StudioError("Wait for the current Social Studio request to finish.")
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            digest = hashlib.sha256(panel["workspace"].encode()).hexdigest()
            path = self.root / "projects" / (digest + ".json")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(path.with_suffix(".lock"), "a") as lock_file:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise StudioError("Another Social Studio panel is using this project. Try again when it finishes.") from None
                document = read_document(path) if path.exists() else empty_document()
                yield document, path
        finally:
            self.lock.release()

    def commit(self, document, path, cancelled):
        validate_document(document)
        write_json(path, document, cancelled)

    def client(self, document, cancelled):
        connection = document.get("connection")
        if not connection:
            raise StudioError("Connect OpenPost in Accounts first.")
        token = self.keychain().load(connection["credentialID"])
        if not token:
            raise StudioError("The OpenPost token is unavailable. Reconnect in Accounts.")
        return self.client_factory(connection["origin"], token, cancelled), connection

    @staticmethod
    def draft(document, identifier):
        for draft in document["drafts"]:
            if draft["id"] == identifier:
                return draft
        raise StudioError("This local draft no longer exists.")

    def legacy_sources(self, workspace):
        digest = hashlib.sha256(workspace.encode()).hexdigest()
        return [edition for edition in ("Locus", "LocusX") if (self.legacy_root / edition / "Social Studio" / (digest + ".json")).is_file()]

    def call(self, operation, payload, metadata, cancelled=None):
        cancelled = cancelled or threading.Event()
        panel = self.panel_context(metadata)
        if not isinstance(payload, dict) or len(json_bytes(payload)) > 256 * 1024:
            raise StudioError("This request is too large. Save one field at a time.")
        try:
            with self.transaction(panel, cancelled) as (document, path):
                result = self._call(operation, payload, panel, document, path, cancelled)
            check_cancelled(cancelled)
            if len(json_bytes(result).decode()) > MAX_RESPONSE:
                raise StudioError("This response exceeds the panel limit. Choose a smaller page or open the publication in OpenPost.")
            return result
        except PlatformError as error:
            raise StudioError(str(error)) from None
        except (KeyError, TypeError, ValueError, OverflowError):
            raise StudioError("The request or saved content is invalid. Refresh Social Studio and try again.") from None

    def _call(self, operation, payload, panel, document, path, cancelled):
        workspace = panel["workspace"]
        session = (workspace, panel["panelId"], panel["digest"])
        self.pending_connections = {k: v for k, v in self.pending_connections.items() if time.monotonic() - v[0] <= 300}
        cache = self.cache.setdefault(workspace, {"accounts": [], "lastSynced": None})
        if operation == "state":
            offset, limit = page_arguments(payload)
            drafts = sorted(document["drafts"], key=lambda d: dt.datetime.fromisoformat(d["updatedAt"].replace("Z", "+00:00")), reverse=True)
            counts = {"drafts": len(drafts), "planned": sum(bool(d.get("plannedAt")) and not d.get("handoff") for d in drafts), "pendingTransfers": sum(bool(d.get("handoff")) and not d["handoff"].get("publicationID") for d in drafts), "transferred": sum(bool(d.get("handoff", {}).get("publicationID")) for d in drafts if d.get("handoff"))}
            search = text_value(payload.get("search", ""), 1000).casefold()
            if search:
                drafts = [d for d in drafts if search in (d["title"] + "\n" + d["text"]).casefold()]
            if payload.get("month"):
                month = text_value(payload["month"], 7)
                drafts = [d for d in drafts if str(d.get("plannedAt", "")).startswith(month)]
            return {"workspace": workspace, "drafts": [public_draft(d, True) for d in drafts[offset:offset+limit]], "total": len(drafts), "counts": counts, "brand": document["brand"], "connection": public_connection(document.get("connection")), "legacySources": self.legacy_sources(workspace), **cache}
        if operation == "draft_get":
            draft = self.draft(document, payload["id"])
            field = payload.get("field")
            if field is not None:
                if not isinstance(field, str) or not field.startswith("variant:") or field[8:] not in CHANNELS:
                    raise StudioError("Select a valid platform version.")
                return {"field": field, "value": draft["variants"].get(field[8:])}
            return {"draft": public_draft(draft)}
        if operation in ("draft_create", "draft_duplicate"):
            if operation == "draft_duplicate":
                draft = copy.deepcopy(self.draft(document, payload["id"]))
                draft["title"] = (draft["title"].strip() or "Untitled post")[:193] + " · copy"
                draft.pop("handoff", None)
                draft.pop("plannedAt", None)
            else:
                draft = {"title": "", "text": "", "channels": ["linkedin"], "variants": {}}
            draft.update(id=str(uuid.uuid4()), updatedAt=now())
            document["drafts"].insert(0, draft)
            self.commit(document, path, cancelled)
            return {"draft": public_draft(draft)}
        if operation == "draft_update":
            draft = self.draft(document, payload["id"])
            if draft.get("handoff"):
                raise StudioError("This draft has been handed to OpenPost. Duplicate it to make a new version.")
            field, value = payload["field"], payload.get("value")
            if field in ("text", "title"):
                draft[field] = text_value(value, 200 if field == "title" else 50_000)
            elif field == "channels":
                if not isinstance(value, list) or any(c not in CHANNELS for c in value) or len(value) != len(set(value)):
                    raise StudioError("Select valid social channels.")
                draft[field] = value
            elif field == "plannedAt":
                draft[field] = date_string(value)
            elif field.startswith("variant:") and field[8:] in CHANNELS:
                if value is None:
                    draft["variants"].pop(field[8:], None)
                else:
                    draft["variants"][field[8:]] = text_value(value)
            else:
                raise StudioError("This draft field cannot be changed.")
            draft["updatedAt"] = now()
            self.commit(document, path, cancelled)
            return {"draft": public_draft(draft)}
        if operation == "draft_delete":
            draft = self.draft(document, payload["id"])
            document["drafts"].remove(draft)
            self.commit(document, path, cancelled)
            return {"deleted": draft["id"]}
        if operation == "brand_update":
            field = payload["field"]
            if field not in document["brand"]:
                raise StudioError("This brand field cannot be changed.")
            document["brand"][field] = text_value(payload["value"])
            self.commit(document, path, cancelled)
            return {"brand": document["brand"]}
        if operation == "connect":
            self.pending_connections.pop(session, None)
            origin = validated_origin(payload["origin"])
            token = self.platform.prompt_token(cancelled)
            found = self.client_factory(origin, token, cancelled).request(["workspaces"]) or []
            workspaces = [{"id": resource_id(w["id"]), "name": text_value(w["name"], 1000), "can_edit": bool(w.get("can_edit"))} for w in found]
            if not workspaces:
                raise StudioError("This token has no accessible workspaces. Check its workspace access in OpenPost.")
            check_cancelled(cancelled)
            self.pending_connections[session] = (time.monotonic(), origin, token, workspaces)
            return {"workspaces": workspaces}
        if operation == "connect_workspace":
            pending = self.pending_connections.pop(session, None)
            if not pending or time.monotonic() - pending[0] > 300:
                raise StudioError("Find workspaces again to reconnect OpenPost.")
            _, origin, token, workspaces = pending
            selected = next((w for w in workspaces if w["id"] == payload["workspaceId"]), None)
            if selected is None:
                raise StudioError("Select one of the workspaces returned by OpenPost.")
            identifier = "social-studio." + str(uuid.uuid4())
            old = document.get("connection")
            self.keychain().save(identifier, token)
            try:
                document["connection"] = {"origin": origin, "workspaceID": selected["id"], "workspaceName": selected["name"], "credentialID": identifier}
                self.commit(document, path, cancelled)
            except BaseException:
                self.keychain().delete(identifier)
                raise
            if old:
                self.keychain().delete(old["credentialID"])
            cache.update(accounts=[], lastSynced=None)
            self.reviews = {k: v for k, v in self.reviews.items() if k[0] != workspace}
            return {"connection": public_connection(document["connection"])}
        if operation == "disconnect":
            old = document.get("connection")
            document["connection"] = None
            self.commit(document, path, cancelled)
            if old:
                self.keychain().delete(old["credentialID"])
            self.pending_connections.pop(session, None)
            cache.update(accounts=[], lastSynced=None)
            self.reviews = {k: v for k, v in self.reviews.items() if k[0] != workspace}
            return {"connection": None}
        if operation in ("refresh", "publications"):
            client, connection = self.client(document, cancelled)
            offset, limit = page_arguments(payload)
            if operation == "refresh":
                accounts = client.request(["accounts"], query={"workspace_id": connection["workspaceID"]}) or []
                cache["accounts"] = [{k: a.get(k) for k in ("id", "platform", "account_username", "is_active")} for a in accounts]
            publications = client.request(["publications"], query={"workspace_id": connection["workspaceID"], "limit": limit, "offset": offset}) or []
            if any(p["workspace_id"] != connection["workspaceID"] for p in publications):
                raise StudioError("OpenPost returned publications from another workspace.")
            cache["lastSynced"] = now()
            return {"publications": [publication_summary(p) for p in publications], "hasMore": len(publications) == limit, "offset": offset, **cache}
        if operation == "publication_get":
            client, connection = self.client(document, cancelled)
            publication = client.request(["publications", resource_id(payload["id"])])
            if publication["workspace_id"] != connection["workspaceID"]:
                raise StudioError("This publication belongs to another workspace.")
            if len(json_bytes({"publication": publication}).decode()) > MAX_RESPONSE:
                raise StudioError("This response exceeds the panel limit. Open the publication in OpenPost.")
            # Keep the exact revision shown to this panel, never substitute fresh
            # content when the user subsequently confirms a publish action.
            if len(self.reviews) >= 100:
                self.reviews.pop(next(iter(self.reviews)))
            self.reviews[session + (publication["id"], publication["revision"])] = (copy.deepcopy(connection), copy.deepcopy(publication))
            return {"publication": publication}
        if operation == "send_draft":
            client, connection = self.client(document, cancelled)
            draft = self.draft(document, payload["id"])
            if not draft.get("handoff"):
                if not draft["text"].strip():
                    raise StudioError("Write a post before sending it to OpenPost.")
                ids = payload.get("accountIds", [])
                if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
                    raise StudioError("Select valid destinations.")
                accounts = client.request(["accounts"], query={"workspace_id": connection["workspaceID"]}) or [] if ids else []
                selected = [a for a in accounts if a["id"] in ids and a["is_active"]]
                if len(selected) != len(ids):
                    raise StudioError("An account is unavailable. Refresh Accounts and choose again.")
                body = {"workspace_id": connection["workspaceID"], "title": draft["title"].strip() or "Untitled post", "content_profile": "short_text", "creation_preset": "post", "source_text": draft["text"], "renditions": [{"social_account_id": a["id"], "body": draft["variants"].get(a["platform"], draft["text"])} for a in selected]}
                if draft.get("plannedAt"):
                    body["scheduled_at"] = draft["plannedAt"]
                draft["handoff"] = {"origin": connection["origin"], "workspaceID": connection["workspaceID"], "key": "locus-draft-" + str(uuid.uuid4()), "body": base64.b64encode(json_bytes(body)).decode()}
                self.commit(document, path, cancelled)
            handoff = draft["handoff"]
            if handoff["origin"] != connection["origin"] or handoff["workspaceID"] != connection["workspaceID"]:
                raise StudioError("This draft belongs to another OpenPost workspace. Reconnect that workspace or duplicate the draft.")
            if handoff.get("publicationID"):
                return {"draft": public_draft(draft), "notice": "This draft is already in OpenPost. Find it in Activity."}
            publication = client.request(["publications"], method="POST", body=base64.b64decode(handoff["body"], validate=True), key=handoff["key"])
            if publication["workspace_id"] != connection["workspaceID"]:
                raise StudioError("OpenPost returned a publication from another workspace. The saved transfer remains pending.")
            handoff["publicationID"] = resource_id(publication["id"])
            self.commit(document, path, cancelled)
            return {"draft": public_draft(draft), "publication": publication_summary(publication), "notice": "Draft sent to OpenPost. Review it in Activity to schedule or publish."}
        if operation == "publication_action":
            client, connection = self.client(document, cancelled)
            identifier, revision, action = resource_id(payload["id"]), payload["revision"], payload["action"]
            if type(revision) is not int or revision < 1 or action not in ("schedule", "publish-now", "cancel"):
                raise StudioError("Select a valid publication action.")
            reviewed = self.reviews.get(session + (identifier, revision))
            if not reviewed or reviewed[0] != connection:
                raise StudioError("Open this publication and review its current revision before taking an action.")
            publication = reviewed[1]
            if publication["workspace_id"] != connection["workspaceID"]:
                raise StudioError("This publication belongs to another workspace.")
            if action == "schedule":
                scheduled = date_string(publication.get("scheduled_at"))
                if not scheduled or dt.datetime.fromisoformat(scheduled.replace("Z", "+00:00")) <= dt.datetime.now(dt.timezone.utc):
                    raise StudioError("Choose a future schedule in OpenPost, then refresh Activity.")
            if action != "cancel":
                validation = client.request(["publications", identifier, "validate"], method="POST", body=b"{}")
                if not validation.get("valid"):
                    # Do not reflect arbitrary provider text that could include credentials.
                    raise StudioError("OpenPost validation failed. Review the publication's content and destinations in OpenPost.")
            result = client.request(["publications", identifier, action], method="POST", body=json_bytes({"expected_revision": revision}), key=f"locus-{identifier}-{revision}-{action}")
            check_cancelled(cancelled)
            notice = "Cancellation accepted. Refresh to check the latest status." if action == "cancel" else "OpenPost accepted the request. Refresh to check each destination's result."
            if result.get("job_id"):
                notice += " Job: " + resource_id(result["job_id"])
            try:
                updated = client.request(["publications", identifier])
                if updated["workspace_id"] != connection["workspaceID"]:
                    raise StudioError("The updated publication belongs to another workspace.")
                return {"publication": publication_summary(updated), "notice": notice}
            except StudioError:
                check_cancelled(cancelled)
                return {"notice": notice + " The latest status could not be loaded. Refresh Activity."}
        if operation == "import_legacy":
            edition = payload["edition"]
            if edition not in ("Locus", "LocusX"):
                raise StudioError("Select a Locus or LocusX data source.")
            if document["drafts"] or document.get("connection") or document["brand"] != empty_document()["brand"]:
                raise StudioError("Import is available for an empty Social Studio project. Export current drafts before replacing any data.")
            digest = hashlib.sha256(workspace.encode()).hexdigest()
            imported = read_document(self.legacy_root / edition / "Social Studio" / (digest + ".json"), legacy=True)
            imported["version"] = 2
            imported["connection"] = None
            for draft in imported["drafts"]:
                draft["updatedAt"] = date_string(draft["updatedAt"], legacy=True)
                if draft.get("plannedAt") is not None:
                    draft["plannedAt"] = date_string(draft["plannedAt"], legacy=True)
            # Exact base64 retry bodies, UUIDs and keys survive migration untouched.
            self.commit(imported, path, cancelled)
            return {"imported": len(imported["drafts"]), "notice": "Imported local content. Reconnect OpenPost in Accounts. Original files and credentials were preserved."}
        if operation == "copy":
            draft = self.draft(document, payload["id"])
            channel = payload.get("channel")
            if channel is not None and channel not in CHANNELS:
                raise StudioError("Select a valid social channel.")
            check_cancelled(cancelled)
            self.platform.copy(draft["variants"].get(channel, draft["text"]))
            return {"notice": "Copied to clipboard."}
        if operation == "export":
            target = Path(self.platform.export_path(cancelled))
            content = copy.deepcopy(document)
            content["connection"] = None
            for draft in content["drafts"]:
                draft.pop("handoff", None)
            write_json(target, content, cancelled)
            return {"notice": "Drafts exported without connection details or transfer envelopes."}
        if operation == "assistant_prompt":
            action, topic = payload["action"], text_value(payload.get("topic", ""), 10_000)
            brand = document["brand"]
            if action == "research":
                request = f"Use the last30days skill to research {topic}. Focus on the last 30 days. Include dated source links, recurring questions, emerging discussions, and 5 specific social post angles. Distinguish evidence from suggestions. If a source or the skill is unavailable, say so."
            elif action == "ideas":
                request = f"Develop 5 concrete social post ideas about {topic}. Give each a hook, useful takeaway, recommended channel, and a draft. Do not invent statistics or customer claims."
            elif action == "adapt":
                draft = self.draft(document, payload["id"])
                request = f"Adapt this draft for {', '.join(CHANNELS[c] for c in draft['channels'])}. Keep the facts intact and follow each platform's current constraints. Return a clearly labeled version for each channel.\n\nDraft: {draft['text']}"
            else:
                raise StudioError("Select a valid assistant action.")
            return {"prompt": request + f"\n\nBrand: {brand['name']}\nAudience: {brand['audience']}\nVoice: {brand['voice']}\nContent themes: {brand['topics']}\n\nPrepare content for review in Locus Social Studio. Do not schedule or publish anything."}
        if operation == "open":
            target = payload["target"]
            origin = validated_origin((document.get("connection") or {}).get("origin", "https://app.openpo.st"))
            if target == "docs":
                url = "https://openpo.st/docs/api-reference"
            elif target in ("openpost", "developer-tokens", "publication"):
                # Instance UI routes are not an API contract; open its landing
                # page rather than guessing deep links that may change.
                url = origin
            elif target == "rendition":
                client, connection = self.client(document, cancelled)
                publication = client.request(["publications", resource_id(payload["id"])])
                if publication["workspace_id"] != connection["workspaceID"]:
                    raise StudioError("This publication belongs to another workspace.")
                rendition = next((r for r in publication.get("renditions") or [] if r["id"] == payload["renditionId"]), None)
                url = (rendition or {}).get("external_url", "")
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    raise StudioError("This destination has no published HTTPS link yet.")
            else:
                raise StudioError("Select a valid browser destination.")
            check_cancelled(cancelled)
            self.platform.open(url)
            return {"notice": "Opened in your browser."}
        raise StudioError("Unknown Social Studio operation.")
