import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {resolve, extname, sep} from 'node:path';
import {createRequire} from 'node:module';

const require = createRequire(new URL('../dev/package.json', import.meta.url));
const {chromium} = process.env.PLAYWRIGHT_MODULE ? await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE)) : require('playwright');
const root = fileURLToPath(new URL('../ui/', import.meta.url));
const server = createServer(async (request, response) => {
  const file = resolve(root, `.${decodeURIComponent(new URL(request.url, 'http://localhost').pathname === '/' ? '/index.html' : new URL(request.url, 'http://localhost').pathname)}`);
  if (!file.startsWith(root.endsWith(sep) ? root : root + sep)) { response.writeHead(403).end(); return; }
  try {
    response.writeHead(200, {'Content-Type': {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}[extname(file)] || 'application/octet-stream'});
    response.end(await readFile(file));
  } catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const url = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {})});
let page;

function installMock({legacy = false} = {}) {
  const copy = value => JSON.parse(JSON.stringify(value));
  const db = window.__studio = {drafts: [], brand: {name: '', audience: '', voice: 'Clear, useful, conversational. Avoid hype.', topics: ''}, connection: null, accounts: [], publications: [], calls: [], messages: [], prompts: [], counter: 0, large: false};
  const account = {id: 'linkedin-one', platform: 'linkedin', account_username: 'studio', is_active: true};
  function tool(operation, payload) {
    const draft = db.drafts.find(value => value.id === payload.id);
    const post = db.publications.find(value => value.id === payload.id);
    switch (operation) {
      case 'state': {
        let drafts = db.drafts;
        if (payload.search) drafts = drafts.filter(value => (value.title + value.text).toLowerCase().includes(payload.search.toLowerCase()));
        if (payload.month) drafts = drafts.filter(value => (value.plannedAt || '').startsWith(payload.month));
        return {drafts: drafts.slice(payload.offset || 0, (payload.offset || 0) + (payload.limit || 100)).map(value => ({...value, text: value.text.slice(0, 240), variants: undefined})), total: drafts.length, counts: {drafts: db.drafts.length, planned: db.drafts.filter(value => value.plannedAt).length, transferred: db.drafts.filter(value => value.handoff?.publicationID).length, pendingTransfers: db.drafts.filter(value => value.handoff && !value.handoff.publicationID).length}, brand: db.brand, connection: db.connection, accounts: db.accounts, legacySources: ['Locus'], workspace: '/projects/studio', lastSynced: '2026-10-06T12:00:00Z'};
      }
      case 'draft_create': { const value = {id: `draft-${++db.counter}`, title: '', text: '', channels: ['linkedin'], variants: {}, plannedAt: null, handoff: null}; db.drafts.unshift(value); return {draft: value}; }
      case 'draft_get': {
        if (payload.field) return {field: payload.field, value: draft.variants[payload.field.slice(8)]};
        if (db.large) return {draft: {...draft, variants: {}, variantsOmitted: true, variantChannels: Object.keys(draft.variants)}};
        return {draft};
      }
      case 'draft_update': {
        if (draft.handoff) throw new Error('This draft has a saved transfer. Duplicate it to edit.');
        if (payload.field.startsWith('variant:')) { if (payload.value === null) delete draft.variants[payload.field.slice(8)]; else draft.variants[payload.field.slice(8)] = payload.value; }
        else draft[payload.field] = payload.value;
        return {draft};
      }
      case 'draft_duplicate': { const value = {...copy(draft), id: `draft-${++db.counter}`, title: draft.title + ' · copy', plannedAt: null, handoff: null}; db.drafts.unshift(value); return {draft: value}; }
      case 'draft_delete': db.drafts = db.drafts.filter(value => value.id !== payload.id); return {deleted: payload.id};
      case 'brand_update': db.brand[payload.field] = payload.value; return {brand: db.brand};
      case 'assistant_prompt': return {prompt: `${payload.action}: ${payload.topic || draft.text}\nBrand: ${db.brand.name}\nDo not schedule or publish anything.`};
      case 'connect': return {workspaces: [{id: 'workspace-1', name: 'Studio workspace', can_edit: true}]};
      case 'connect_workspace': db.connection = {origin: 'https://app.openpo.st', workspaceID: payload.workspaceId, workspaceName: 'Studio workspace'}; db.accounts = [account]; return {connection: db.connection};
      case 'disconnect': db.connection = null; db.accounts = []; return {connection: null};
      case 'refresh': return {publications: db.publications, accounts: db.accounts};
      case 'publications': return {publications: db.publications};
      case 'publication_get': return {publication: post};
      case 'send_draft': {
        draft.handoff ||= {origin: 'https://app.openpo.st', workspaceID: 'workspace-1', publicationID: null};
        if (db.failTransfer) { db.failTransfer = false; throw new Error('Connection interrupted. Transfer outcome is unknown. Retry the saved transfer.'); }
        const value = {id: `post-${draft.id}`, workspace_id: 'workspace-1', title: draft.title, source_text: draft.text, revision: 1, status: 'draft', scheduled_at: draft.plannedAt, renditions: [{id: 'rendition-1', platform: 'linkedin', status: 'draft', social_account_id: account.id, body: draft.variants.linkedin || draft.text}]};
        if (!draft.handoff.publicationID) db.publications.push(value);
        draft.handoff.publicationID = value.id;
        return {publication: value, notice: 'Draft sent to OpenPost.'};
      }
      case 'publication_action': {
        if (payload.revision !== post.revision) throw new Error('The publication changed. Reload and review it before trying again.');
        post.status = payload.action === 'publish-now' ? 'queued' : payload.action === 'schedule' ? 'scheduled' : 'draft';
        post.revision += 1;
        return {publication: post, notice: 'OpenPost accepted the request. Check each destination’s delivery state.'};
      }
      case 'copy': case 'export': case 'open': return {notice: 'Done.'};
      case 'import_legacy': return {imported: 1, notice: 'Imported local content. Reconnect OpenPost.'};
      default: throw new Error(`Unexpected operation ${operation}`);
    }
  }
  window.webkit = {messageHandlers: {locusPanel: {postMessage(message) {
    db.messages.push(copy(message));
    if (message.type === 'ready') queueMicrotask(() => window.locusPanel.receive({version: 1, type: 'hello', workspace: '/projects/studio', project: 'Studio project', panel: 'social-studio', capabilities: ['plugin.tools', 'chat.compose'], ...(legacy ? {} : {toolContextVersion: 1})}));
    if (message.type === 'composeChat') db.prompts.push(message.text);
    if (message.type === 'callTool') {
      db.calls.push(copy(message.arguments));
      queueMicrotask(() => {
        try { const result = tool(message.arguments.operation, message.arguments.payload); window.locusPanel.receive({version: 1, type: 'response', requestID: message.requestID, ok: true, result: {content: JSON.stringify(result), is_error: false}}); }
        catch (error) { window.locusPanel.receive({version: 1, type: 'response', requestID: message.requestID, ok: true, result: {content: error.message, is_error: true}}); }
      });
    }
  }}}};
}

async function click(action, within = page) { await within.locator(`[data-action="${action}"]`).first().click(); }
async function section(name) { await page.locator(`nav [data-section="${name}"]`).click(); await page.locator('body:not(.busy)').waitFor(); }
async function calls(operation) { return page.evaluate(operation => window.__studio.calls.filter(call => call.operation === operation), operation); }
async function screenshot(name) {
  if (!process.env.UI_SCREENSHOT_DIR) return;
  await mkdir(process.env.UI_SCREENSHOT_DIR, {recursive: true});
  await page.screenshot({path: resolve(process.env.UI_SCREENSHOT_DIR, name + '.png'), fullPage: true});
}

try {
  const oldPage = await browser.newPage();
  await oldPage.addInitScript(installMock, {legacy: true});
  await oldPage.goto(url);
  await oldPage.getByRole('alert').filter({hasText: 'Update Locus'}).waitFor();
  assert.equal(await oldPage.evaluate(() => window.__studio.calls.length), 0);
  await oldPage.close();

  page = await browser.newPage({viewport: {width: 1220, height: 850}, timezoneId: 'America/Toronto', colorScheme: 'light'});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(installMock, {});
  await page.goto(url);
  await page.getByText('Your next post starts here', {exact: true}).waitFor();
  await screenshot('studio-empty-light');
  await click('new');
  await page.locator('#draft-title').fill('A thoughtful product update <img src=x onerror=alert(1)>');
  await page.locator('#draft-text').fill('We shipped a calmer way to plan your week.\n\nLocal writing, thoughtful publishing.');
  await page.locator('input[data-channel="x"]').check();
  await page.getByRole('tab', {name: 'X', exact: true}).click();
  await page.locator('#draft-text').fill('A calmer way to plan your week. Now in Social Studio.');
  await screenshot('studio-composer-light');
  await click('save-draft');
  await page.locator('[data-draft="draft-1"]').waitFor();
  assert.equal(await page.locator('img').count(), 0);
  assert.equal(await page.evaluate(() => window.__studio.drafts[0].variants.x), 'A calmer way to plan your week. Now in Social Studio.');

  await click('edit');
  await page.getByRole('tab', {name: 'X', exact: true}).click();
  assert.equal(await page.locator('#draft-text').inputValue(), 'A calmer way to plan your week. Now in Social Studio.');
  await click('original');
  assert.match(await page.locator('#draft-text').inputValue(), /We shipped/);
  await click('save-draft');
  assert.equal(await page.evaluate(() => window.__studio.drafts[0].variants.x), undefined);

  await section('brand');
  await page.locator('[name="name"]').fill('Studio Notes');
  await page.locator('[name="audience"]').fill('Independent makers and thoughtful teams');
  await click('save-brand');
  assert.equal(await page.evaluate(() => window.__studio.brand.name), 'Studio Notes');
  await section('research');
  await page.locator('#topic').fill('Calmer software for creative teams');
  await page.locator('[data-kind="research"]').click();
  assert.match(await page.evaluate(() => window.__studio.prompts.at(-1)), /research.*Calmer software/s);
  assert.match(await page.evaluate(() => window.__studio.prompts.at(-1)), /Brand: Studio Notes/);
  assert.equal((await calls('publication_action')).length, 0);
  await screenshot('studio-research-light');

  await section('drafts');
  await click('edit');
  await page.locator('#plan-enabled').check();
  await page.locator('#planned-at').fill('');
  await click('save-draft');
  await page.locator('.sheet-error').filter({hasText: 'valid local planned date'}).waitFor();
  await page.locator('#plan-enabled').uncheck();
  assert.equal(await page.locator('#planned-at').count(), 0);
  await page.locator('#plan-enabled').check();
  await page.locator('#planned-at').fill('');
  await click('editor-cancel');
  await page.getByRole('heading', {name: 'Discard unsaved changes?'}).waitFor();
  await click('keep-editing');
  await page.locator('#planned-at').fill('2027-02-14T09:30');
  await click('adapt');
  assert.match(await page.evaluate(() => window.__studio.prompts.at(-1)), /adapt: We shipped/);
  assert.equal(await page.evaluate(() => window.__studio.drafts[0].plannedAt), '2027-02-14T14:30:00.000Z');
  await click('save-draft');

  await section('accounts');
  await click('connect');
  assert.equal(await page.locator('input[type="password"]').count(), 0);
  await click('find-workspaces');
  await page.locator('#connection-workspace').waitFor();
  assert.deepEqual((await calls('connect')).at(-1).payload, {origin: 'https://app.openpo.st'});
  await click('connect-workspace');
  await page.getByText('LinkedIn · @studio', {exact: true}).waitFor();
  await screenshot('studio-accounts-light');

  await section('drafts');
  await page.evaluate(() => { window.__studio.failTransfer = true; });
  await click('transfer');
  await page.locator('input[name="destination"]').check();
  await click('send-draft');
  await page.locator('.sheet-error').filter({hasText: 'outcome is unknown'}).waitFor();
  assert.equal((await calls('send_draft')).length, 1);
  await click('close', page.locator('#modal'));
  // Returning to Drafts reads the persisted handoff and freezes the card.
  await section('drafts');
  await page.getByRole('button', {name: 'Retry transfer', exact: true}).waitFor();
  assert.equal(await page.locator('[data-draft="draft-1"] [data-action="edit"]').count(), 0);
  await click('transfer');
  await page.getByText('Retries the exact saved content', {exact: false}).waitFor();
  assert.equal(await page.locator('input[name="destination"]').count(), 0);
  await click('send-draft');
  assert.equal((await calls('send_draft')).length, 2);
  await section('activity');
  await screenshot('studio-activity-light');
  await page.locator('[data-kind="publish-now"]').click();
  await page.locator('#modal').getByText('Reviewed revision: 1.', {exact: false}).waitFor();
  await page.evaluate(() => { window.__studio.publications[0].revision = 2; window.__studio.publications[0].source_text = 'A revised version for review.'; });
  await click('publication-action');
  await page.locator('.sheet-error').filter({hasText: 'publication changed'}).waitFor();
  assert.equal((await calls('publication_action')).length, 1);
  await page.locator('#modal [data-action="review"]').click();
  await page.locator('#modal').getByText('Reviewed revision: 2.', {exact: false}).waitFor();
  await click('publication-action');
  await page.locator('.publication .badge').filter({hasText: 'queued'}).first().waitFor();
  assert.deepEqual((await calls('publication_action')).map(call => call.payload.revision), [1, 2]);

  await page.evaluate(() => { const post = window.__studio.publications[0]; post.status = 'draft'; post.renditions[0].status = 'failed'; post.renditions[0].error_message = 'Reconnect this account'; post.renditions[0].external_url = 'https://example.com/published'; });
  await click('refresh');
  await page.getByText('Reconnect this account', {exact: true}).waitFor();
  await page.getByRole('button', {name: 'View post ↗', exact: true}).click();
  assert.equal((await calls('open')).at(-1).payload.target, 'rendition');
  await page.locator('[data-kind="schedule"]').click();
  await click('publication-action');
  await page.getByRole('button', {name: 'Cancel schedule…', exact: true}).waitFor();
  await page.locator('[data-kind="cancel"]').click();
  await click('publication-action');
  assert.deepEqual((await calls('publication_action')).slice(-2).map(call => call.payload.action), ['schedule', 'cancel']);

  await section('drafts');
  await click('duplicate');
  await page.locator('#modal-title').filter({hasText: 'Compose a post'}).waitFor();
  assert.equal(await page.evaluate(() => window.__studio.drafts[0].handoff), null);
  await click('editor-cancel');
  await page.locator('[data-draft="draft-2"]').waitFor();
  await page.locator('#search').fill('missing word');
  await page.getByText('No matching drafts', {exact: true}).waitFor();
  await page.locator('#search').fill('thoughtful');
  await page.locator('[data-draft="draft-2"]').waitFor();
  await click('copy');
  await click('export');
  assert.equal((await calls('copy')).length, 1);
  assert.equal((await calls('export')).length, 1);

  // Large field responses are fetched separately and survive editing intact.
  await page.evaluate(() => { window.__studio.large = true; window.__studio.drafts[0].variants.x = 'A large-response variant that must survive.'; });
  await page.locator('[data-draft="draft-2"] [data-action="edit"]').click();
  await page.getByRole('tab', {name: 'X', exact: true}).click();
  assert.equal(await page.locator('#draft-text').inputValue(), 'A large-response variant that must survive.');
  await page.locator('#draft-title').fill('A fresh direction');
  await click('save-draft');
  assert.equal(await page.evaluate(() => window.__studio.drafts[0].variants.x), 'A large-response variant that must survive.');
  assert.ok((await calls('draft_get')).some(call => call.payload.field === 'variant:x'));

  await page.locator('#search').fill('');
  await page.locator('[data-draft="draft-2"]').waitFor();
  await page.emulateMedia({colorScheme: 'dark'});
  await page.waitForTimeout(200); // Let the brief button background transition settle for screenshots.
  await screenshot('studio-drafts-dark');
  await page.locator('[data-draft="draft-2"] [data-action="remove"]').click();
  await click('remove-confirm');
  assert.equal(await page.locator('[data-draft="draft-2"]').count(), 0);
  await section('calendar');
  assert.equal(await page.locator('.calendar .day').count(), 42);
  await screenshot('studio-calendar-dark');
  await page.setViewportSize({width: 680, height: 780});
  await section('research');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await screenshot('studio-narrow-dark');
  await section('accounts');
  await page.locator('[data-edition="Locus"]').click();
  await click('import-confirm');
  assert.deepEqual((await calls('import_legacy')).at(-1).payload, {edition: 'Locus'});
  await click('disconnect');
  await click('disconnect-confirm');
  assert.equal(await page.evaluate(() => window.__studio.connection), null);
  assert.deepEqual(errors, []);
  console.log('UI smoke passed: project compatibility gate, local CRUD, channel variants, large field responses, brand, editable assistant handoffs, native connection, frozen transfer retry, revision conflict, schedule/publish/cancel, destination status/links, search, clipboard/export, calendar, import, disconnect, light/dark and narrow layout.');
} catch (error) {
  if (page) { await screenshot('studio-test-failure'); console.error(await page.locator('body').innerText()); }
  throw error;
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
