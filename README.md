# Social Studio · OpenPost for Locus

An optional social workspace that Locus downloads from this repository. Keep
project drafts, channel versions, brand voice, and a content calendar together;
connect an OpenPost workspace when you want to schedule or publish.

The interface, storage, credentials, and publishing adapter belong to this
plugin. They are not compiled into Locus.

## Install and update

1. Open **Manage Plugins → Browse** in Locus.
2. Add **nahid-sparktales/locus-openpost** as a marketplace source, or find
   **Social Studio · OpenPost** in the Locus marketplace.
3. Review the package and install it everywhere or for the current project.
4. Choose **Work → Social Studio…**.

Use a Locus build with plugin panel context support. Older builds show an update
requirement and cannot run this plugin. No Node installation, compilation, or
Python package setup is required to use it. Git and network access are needed
for the existing Locus plugin installer.

To update, find the installed plugin in **Browse**, choose **Review update**, and
review the new contents. Locus retains previous versions for rollback. Updates
are explicit; this repository does not install or update anything automatically.
When upgrading the former built-in plugin, update its existing entry in the
Locus marketplace, then import your project's old drafts from Accounts.

## Write, research, and plan

- Create, search, edit, duplicate, copy, export, or remove local drafts. Each
  project keeps its own drafts and brand voice.
- Keep an original post and separate platform versions. A local planned date
  appears on the calendar; it does not create a publishing job.
- Research recent conversations, brainstorm ideas, or adapt a draft. These
  actions open an editable Locus chat; review it and send it yourself. Research
  requests use Locus's shared `last30days` skill when available.
- The composer focuses on text. Use OpenPost for media editing, inbox,
  engagement analytics, and providers that require images or video.

## Connect and publish

Connect social accounts in OpenPost first. Create a token in **Settings →
Personal → Developer** with the workspace/API permissions you need. In this
plugin's Accounts section, enter the instance origin and choose Connect. A
native secure-entry dialog asks for the token; then select your workspace.

Hosted uses `https://app.openpo.st`. Self-hosted instances require HTTPS except
for HTTP loopback addresses used for local development. Supply the origin
without `/api/v1`. Authenticated redirects are refused.

**Send to OpenPost** creates an unpublished draft. Choose destinations and review
the actual publication in Activity before scheduling or publishing. The action
uses the revision you reviewed and validates the content first. Acceptance is
not delivery: refresh to inspect each destination's status, link, or error.
Cancellation requests cancellation of the remote schedule; a post already being
published may still complete.

Once transfer starts, the local draft is frozen. Duplicate it to write another
version, or edit the remote publication in OpenPost. Retrying an interrupted
transfer reuses its saved body and idempotency key. Reconnect the original
instance and workspace before retrying. Revision conflicts require refreshing
and reviewing the changed publication.

## Existing drafts and privacy

Use the import control to copy this project's data from the former built-in
Locus or LocusX Social Studio. If both editions have data, choose the appropriate
source. Imports preserve drafts, brand settings, planned dates, and pending
transfer envelopes. They never modify the original files or old Keychain items.
Reconnect your OpenPost workspace after import. Corrupt or unknown-version
stores are reported, not replaced with empty data.

New data lives in Locus's plugin data directory, partitioned by canonical
project path. Tokens live in a plugin-owned macOS Keychain service. Token entry
does not pass through the plugin web page, tool arguments, chat, or exports.
Exports contain writing and brand settings only, without connections or retry
envelopes.

All plugin tools are restricted to its window and hidden from agents. Disabling
or uninstalling closes its window and revokes further requests. Jobs already
accepted by OpenPost continue there. Uninstallation retains local plugin data;
disconnect first if you want to remove the plugin's stored token. Disconnecting
does not revoke the token on OpenPost or cancel remote jobs.

## Development

The package ships static HTML/CSS/JavaScript and a Python backend using
`mcp==2.0.0`, already included in Locus. Everything else in the runtime backend
uses the Python standard library and built-in macOS facilities. Keychain access
uses Security/CoreFoundation; clipboard, browser, and save dialogs use system
executables. Tests replace these interfaces and remote HTTP with fixtures.

The manifest declares one panel-only `social_studio` tool and `chat.compose`.
Locus supplies the captured project and installed digest in MCP request metadata
under `com.locus/panel`. The backend requires this context rather than trusting
the process working directory or a workspace supplied by the web page.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
npm --prefix dev ci
npm --prefix dev exec -- playwright install chromium
npm --prefix dev test
npm --prefix dev run test:ui
```

Browser testing additionally needs the Playwright browser installation described
by its test runner. These tools are for development only and are not installed
when Locus downloads the plugin.

## Provenance

This is an original Apache-2.0 adapter extracted from
[Locus](https://github.com/nahid-sparktales/locus), preserving its Social Studio
workflow. No upstream OpenPost source or assets are bundled. This is not an
official OpenPost product. The separate service is maintained and licensed by
its authors; see its [HTTP API](https://openpo.st/docs/api-reference).
