import test from 'node:test';
import assert from 'node:assert/strict';
import {checkedArguments, checkedPrompt, PanelBridge, unpackResult} from '../ui/bridge.js';
import {escapeHTML, draftChanges, textForChannel, monthDays, dateKey, normalizePublication, normalizeAccount, draftStatus} from '../ui/model.js';

test('draft variants retain explicit empty strings, and removing a variant restores original', () => {
  const base = {title: 'Title', text: 'Original', channels: ['linkedin', 'x'], variants: {x: '', linkedin: 'LinkedIn'}, plannedAt: null};
  assert.equal(textForChannel(base, 'x'), '');
  const changed = structuredClone(base); delete changed.variants.x; changed.plannedAt = '2026-10-12T13:00:00Z';
  assert.equal(textForChannel(changed, 'x'), 'Original');
  assert.deepEqual(draftChanges(base, changed), [{field: 'plannedAt', value: changed.plannedAt}, {field: 'variant:x', value: null}]);
});

test('draft status distinguishes uncertain transfers from completed handoffs', () => {
  assert.equal(draftStatus({}), 'Draft');
  assert.equal(draftStatus({plannedAt: '2026-10-12T13:00:00Z'}), 'Planned locally');
  assert.equal(draftStatus({handoff: {publicationID: null}}), 'Transfer pending');
  assert.equal(draftStatus({handoff: {publicationID: 'publication-1'}}), 'In OpenPost');
});

test('untrusted content and identifiers are escaped as inert text', () => {
  assert.equal(escapeHTML('<script a="x">&\'hi\'</script>'), '&lt;script a=&quot;x&quot;&gt;&amp;&#39;hi&#39;&lt;/script&gt;');
});

test('calendar spans full weeks and handles year and leap-day boundaries', () => {
  const january = monthDays(new Date(2027, 0, 15));
  assert.equal(january.length, 42);
  assert.equal(january[0].getDay(), 0);
  assert.equal(dateKey(january[0]), '2026-12-27');
  assert.ok(monthDays(new Date(2028, 1, 10)).some(day => dateKey(day) === '2028-02-29'));
});

test('wire normalization preserves destination errors, links and delivery state', () => {
  const publication = normalizePublication({source_text: 'Original', scheduled_at: '2026-10-12T13:00:00Z', revision: 8, renditions: [{platform: 'x', status: 'failed', error_message: 'Rate limited', external_url: 'https://example.com/post', body: 'Variant', social_account_id: 'account-1', delivery: {attempts: 1}}]});
  assert.equal(publication.renditions[0].errorMessage, 'Rate limited');
  assert.equal(publication.renditions[0].body, 'Variant');
  assert.equal(publication.renditions[0].status, 'failed');
  assert.equal(publication.renditions[0].delivery.attempts, 1);
  assert.equal(normalizeAccount({is_active: false, account_username: 'team'}).isActive, false);
});

test('bridge limits count UTF-8 bytes and reject oversized assistant content without truncating', () => {
  assert.throws(() => checkedArguments('draft_update', {value: '🦋'.repeat(70000)}), /256 KiB/);
  assert.equal(checkedArguments('draft_update', {value: '🦋'.repeat(4000)}).payload.value.length, 8000);
  assert.throws(() => checkedPrompt('x'.repeat(16001)), /16,000/);
  assert.equal(checkedPrompt('x'.repeat(16000)).length, 16000);
  assert.throws(() => checkedPrompt('  '), /empty/);
});

function mockHost(hello = {}) {
  const messages = [];
  const host = {webkit: {messageHandlers: {locusPanel: {postMessage(message) {
    messages.push(message);
    if (message.type === 'ready') queueMicrotask(() => host.locusPanel.receive({version: 1, type: 'hello', workspace: '/projects/a', panel: 'social-studio', capabilities: ['plugin.tools', 'chat.compose'], toolContextVersion: 1, ...hello}));
  }}}}};
  return {host, messages};
}

test('unsupported native context cannot issue backend calls', async () => {
  const {host, messages} = mockHost({toolContextVersion: undefined});
  const bridge = new PanelBridge(host);
  await assert.rejects(bridge.start(), /Update Locus/);
  await assert.rejects(bridge.call('state'), /Update Locus/);
  assert.equal(messages.length, 1);
});

test('bridge sends no client-controlled project and keeps assistant handoffs as editable compose messages', async () => {
  const {host, messages} = mockHost();
  const bridge = new PanelBridge(host);
  await bridge.start();
  const call = bridge.call('draft_get', {id: 'one'});
  await Promise.resolve();
  const request = messages.at(-1);
  assert.deepEqual(request.arguments, {operation: 'draft_get', payload: {id: 'one'}});
  assert.equal(request.tool, 'social_studio');
  assert.equal(request.workspace, undefined);
  host.locusPanel.receive({version: 1, type: 'response', requestID: request.requestID, ok: true, result: {content: '{"draft":{"id":"one"}}', is_error: false}});
  assert.equal((await call).draft.id, 'one');
  await bridge.compose('Research this topic; prepare content for review.');
  assert.deepEqual(messages.at(-1), {version: 1, type: 'composeChat', text: 'Research this topic; prepare content for review.'});
});

test('uncertain writes time out once and are never retried automatically', async () => {
  const {host, messages} = mockHost();
  const bridge = new PanelBridge(host, 10);
  await bridge.start();
  await assert.rejects(bridge.call('send_draft', {id: 'one'}), /outcome is unknown/);
  assert.equal(messages.filter(item => item.type === 'callTool').length, 1);
  assert.equal(bridge.pending.size, 0);
});

test('revocation rejects pending operations and prevents further tool calls', async () => {
  const {host, messages} = mockHost();
  const bridge = new PanelBridge(host);
  await bridge.start();
  const call = bridge.call('publication_action', {id: 'one', revision: 3, action: 'publish-now'});
  await Promise.resolve();
  bridge.dispose('Permissions revoked.');
  await assert.rejects(call, /revoked/);
  await assert.rejects(bridge.call('state'), /no longer connected/);
  assert.equal(messages.filter(item => item.type === 'callTool').length, 1);
});

test('malformed/truncated responses and backend errors become explicit errors', () => {
  assert.throws(() => unpackResult({content: '{"draft":', is_error: false}), /incomplete response/);
  assert.throws(() => unpackResult({content: 'Revision changed; review again.', is_error: true}), /Revision changed/);
  assert.deepEqual(unpackResult({content: [{type: 'text', text: '{"total":1}'}]}), {total: 1});
});
