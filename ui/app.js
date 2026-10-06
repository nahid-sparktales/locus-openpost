import {PanelBridge} from './bridge.js';
import {CHANNELS, SECTIONS, escapeHTML as e, displayTitle, textForChannel, publicationID, draftStatus, timezone, formatDate, dateKey, localDateTime, monthDays, normalizeAccount, accountLabel, normalizePublication, draftChanges} from './model.js';

const bridge = new PanelBridge();
const app = document.querySelector('#app');
const modal = document.querySelector('#modal');
const state = {section: 'drafts', search: '', offset: 0, limit: 30, data: {drafts: [], brand: {}, counts: {}, accounts: []}, publications: [], calendarDrafts: [], month: new Date(), busy: false, message: null, editor: null, sheet: null};
const badge = text => `<span class="badge">${e(String(text).replaceAll('_', ' '))}</span>`;
const button = (text, action, extra = '', className = '') => `<button type="button" data-action="${action}" class="${className}" ${extra}>${text}</button>`;
const empty = (title, text, icon = '✎') => `<div class="empty"><span class="empty-icon" aria-hidden="true">${icon}</span><h2>${e(title)}</h2><p>${e(text)}</p></div>`;
const dateLabel = value => value ? `<span class="muted small">${e(formatDate(value))}</span>` : '';
const validID = id => `data-id="${e(id)}"`;
let context;
let searchTimer;
let readGeneration = 0;

function notify(text, error = false) {
  state.message = {text, error};
  document.querySelector('#announcer').textContent = text;
  const banner = document.querySelector('#banner');
  if (banner) banner.innerHTML = `<div class="banner ${error ? 'error' : ''}" role="${error ? 'alert' : 'status'}"><span>${e(text)}</span>${button('×', 'dismiss-message', 'aria-label="Dismiss message"')}</div>`;
}

function setBusy(busy) {
  state.busy = busy;
  document.body.classList.toggle('busy', busy);
  document.querySelector('#working')?.setAttribute('aria-hidden', String(!busy));
  for (const element of document.querySelectorAll('button,input,textarea,select')) {
    if (busy && !element.disabled) { element.disabled = true; element.dataset.busyDisabled = 'true'; }
    if (!busy && element.dataset.busyDisabled) { element.disabled = false; delete element.dataset.busyDisabled; }
  }
}

async function perform(work) {
  if (state.busy) return;
  setBusy(true);
  try { await work(); }
  catch (error) {
    const message = error instanceof Error ? error.message : 'Something went wrong. Try refreshing the studio.';
    notify(message, true);
    const local = modal.querySelector('.sheet-error');
    if (local) { local.textContent = message; local.hidden = false; }
  } finally { setBusy(false); }
}

function applyState(data) {
  state.data = {...state.data, ...data, accounts: (data.accounts || state.data.accounts || []).map(normalizeAccount)};
}

async function loadState() {
  const generation = ++readGeneration;
  const data = await bridge.call('state', {offset: state.offset, limit: state.limit, search: state.search});
  if (generation === readGeneration) applyState(data);
}

async function loadPublications() {
  if (!state.data.connection) { state.publications = []; return; }
  const result = await bridge.call('publications', {offset: 0, limit: 100});
  state.publications = (result.publications || []).map(normalizePublication);
}

async function loadCalendar() {
  const days = monthDays(state.month);
  // Query adjoining months too: UTC dates can fall on a neighboring local day.
  const months = new Set(days.map(day => dateKey(day).slice(0, 7)));
  months.add(days[0].toISOString().slice(0, 7));
  months.add(new Date(+days.at(-1) + 86400000).toISOString().slice(0, 7));
  const drafts = new Map();
  let incomplete = false;
  for (const month of months) {
    let offset = 0;
    while (offset < 2000) {
      const page = await bridge.call('state', {month, offset, limit: 100});
      for (const draft of page.drafts || []) drafts.set(draft.id, draft);
      offset += (page.drafts || []).length;
      if (offset >= (page.total || 0) || !page.drafts?.length) break;
      if (offset >= 2000) incomplete = true;
    }
  }
  state.calendarDrafts = [...drafts.values()];
  if (incomplete) notify('The calendar shows the first 2,000 local plans per month. Use Drafts search to find any additional posts.', true);
}

async function reload({remote = false} = {}) {
  if (remote && state.data.connection) await bridge.call('refresh');
  await loadState();
  if (['calendar', 'activity', 'accounts'].includes(state.section)) await loadPublications();
  if (state.section === 'calendar') await loadCalendar();
  render();
}

function render() {
  const [title, subtitle] = SECTIONS[state.section];
  app.innerHTML = `<aside class="sidebar" aria-label="Social Studio navigation">
    <div class="wordmark"><span class="brand-mark" aria-hidden="true">S</span><div><strong>Social Studio</strong><span class="eyebrow">LOCUS PLUGIN</span></div></div>
    ${button('<span aria-hidden="true">＋</span> New post', 'new', '', 'primary new-post')}
    <nav>${Object.entries(SECTIONS).map(([key, item]) => button(`<span class="nav-icon" aria-hidden="true">${item[2]}</span><span>${e(item[0])}</span>${key === 'drafts' ? `<span class="nav-count">${state.data.counts.drafts ?? state.data.total ?? 0}</span>` : ''}`, 'section', `data-section="${key}" ${state.section === key ? 'aria-current="page"' : ''}`, state.section === key ? 'selected' : '')).join('')}</nav>
    <div class="sidebar-bottom">${button('<strong>✧ Last 30 days</strong><span>Turn recent conversations into your next post.</span>', 'section', 'data-section="research"', 'research-link')}
    <div class="workspace" title="${e(context.workspace)}"><span>▱ ${e(context.project || context.workspace.split('/').filter(Boolean).at(-1))}</span><small>${e(state.data.connection?.workspaceName || 'Local drafts · ready to write')}</small></div></div>
  </aside>
  <main class="main"><header class="header"><div><h1>${e(title)}</h1><p>${e(subtitle)}</p></div><div class="header-actions"><span id="working" class="working" aria-hidden="true" role="status">Working…</span>
    ${state.section === 'drafts' ? `<label class="search"><span class="sr-only">Find a draft</span><span aria-hidden="true">⌕</span><input id="search" type="search" placeholder="Find a draft" value="${e(state.search)}"></label>${button('Export…', 'export', 'aria-label="Export all local drafts"')}` : ''}
    ${['calendar', 'activity', 'accounts'].includes(state.section) ? button('↻ Refresh', 'refresh', state.data.connection ? '' : 'disabled') : ''}
  </div></header><div id="banner"></div><div id="content" class="content">${sectionContent()}</div></main>`;
  if (state.message) notify(state.message.text, state.message.error);
  if (state.busy) setBusy(true);
}

function sectionContent() {
  return ({drafts: draftContent, calendar: calendarContent, research: researchContent, activity: activityContent, accounts: accountsContent, brand: brandContent})[state.section]();
}

function draftContent() {
  const counts = state.data.counts;
  return `<div class="metrics">${[['In your studio', (counts.drafts || 0) - (counts.transferred || 0) - (counts.pendingTransfers || 0)], ['Planned locally', counts.planned || 0], ['Sent to OpenPost', counts.transferred || 0]].map(([label, count]) => `<div class="card metric"><span>${label}</span><strong>${count}</strong></div>`).join('')}</div>
  ${state.data.drafts.length ? `<div class="draft-list">${state.data.drafts.map(draftCard).join('')}</div><div class="pagination"><span class="muted small">${state.offset + 1}–${state.offset + state.data.drafts.length} of ${state.data.total} drafts</span><div>${button('Previous', 'page', `data-offset="${Math.max(0, state.offset - state.limit)}" ${state.offset === 0 ? 'disabled' : ''}`)}${button('Next', 'page', `data-offset="${state.offset + state.limit}" ${state.offset + state.data.drafts.length >= state.data.total ? 'disabled' : ''}`)}</div></div>` : `${empty(state.search ? 'No matching drafts' : 'Your next post starts here', state.search ? 'Try a different word or clear your search.' : 'Capture an idea, make it yours for each channel, then plan when to share it.')}${!state.search ? `<div class="center actions">${button('Write your first post', 'new', '', 'primary')}${button('Find an idea', 'section', 'data-section="research"')}</div><div class="starters">${[['A product update', 'What changed, and why it matters.', 'update'], ['Something you learned', 'Share a useful lesson from the work.', 'lesson'], ['Start a conversation', 'Ask a question people can answer.', 'conversation']].map(([title, detail, id]) => button(`<span aria-hidden="true">↗</span><strong>${title}</strong><span>${detail}</span>`, 'starter', `data-starter="${id}"`, 'card starter')).join('')}</div>` : ''}`}`;
}

function draftCard(draft) {
  return `<article class="card draft-card" data-draft="${e(draft.id)}"><div class="row"><h2>${e(displayTitle(draft))}</h2>${badge(draftStatus(draft))}</div><p class="excerpt">${e(draft.text || 'An idea waiting for its words.')}</p><div class="row wrap"><div class="tags">${(draft.channels || []).map(channel => badge(CHANNELS[channel] || channel)).join('')}${dateLabel(draft.plannedAt)}</div><div class="actions">
    ${button('Duplicate', 'duplicate', validID(draft.id))}${button('Copy', 'copy', validID(draft.id) + ' aria-label="Copy original post text"')}${button('Remove…', 'remove', validID(draft.id), 'subtle')}
    ${!draft.handoff ? button('Edit', 'edit', validID(draft.id)) : ''}${!publicationID(draft) ? button(draft.handoff ? 'Retry transfer' : 'Send to OpenPost', 'transfer', validID(draft.id), 'accent') : button('View activity', 'section', 'data-section="activity"')}
  </div></div></article>`;
}

function calendarContent() {
  const remoteIDs = new Set(state.publications.map(post => post.id));
  const days = monthDays(state.month);
  const today = dateKey(new Date());
  return `<div class="row"><h2>${e(state.month.toLocaleDateString(undefined, {month: 'long', year: 'numeric'}))}</h2><div class="actions">${button('Today', 'month', 'data-delta="0"')}${button('‹', 'month', 'data-delta="-1" aria-label="Previous month"')}${button('›', 'month', 'data-delta="1" aria-label="Next month"')}</div></div><p class="muted">Local plans are reminders, not publishing jobs. Send a draft to OpenPost and schedule it in Activity to publish automatically.</p>
    <div class="calendar">${days.slice(0, 7).map(day => `<div class="weekday">${e(day.toLocaleDateString(undefined, {weekday: 'short'}))}</div>`).join('')}${days.map(day => {
      const key = dateKey(day);
      const drafts = state.calendarDrafts.filter(draft => draft.plannedAt && dateKey(new Date(draft.plannedAt)) === key && !remoteIDs.has(publicationID(draft)));
      const remote = state.publications.filter(post => post.scheduledAt && dateKey(new Date(post.scheduledAt)) === key);
      return `<div class="day ${day.getMonth() !== state.month.getMonth() ? 'outside' : ''} ${key === today ? 'today' : ''}"><div class="row"><span class="day-number">${day.getDate()}</span>${button('＋', 'plan', `data-date="${key}" aria-label="Plan a post for ${e(day.toLocaleDateString(undefined, {dateStyle: 'full'}))}"`)}</div><div class="day-posts">${drafts.map(draft => button(e(displayTitle(draft)), draft.handoff ? 'transfer' : 'edit', validID(draft.id), 'calendar-post')).join('')}${remote.map(post => button(e(post.title || post.sourceText || 'OpenPost publication'), 'section', `data-section="activity" title="${e(post.status)}"`, 'calendar-post remote')).join('')}</div></div>`;
    }).join('')}</div><div class="row wrap muted small"><span>◷ Times shown in ${e(timezone())}</span><span>○ Local plan &nbsp; ● OpenPost · latest 100 publications</span></div>`;
}

function researchContent() {
  return `<div class="card research-card"><span class="eyebrow accent-text">✧ RESEARCH & IDEAS</span><h2>A little context.<br>Better content.</h2><p class="muted">Research what people are discussing, then turn what you learn into something useful for your audience.</p><label class="field"><span>What are you curious about?</span><input id="topic" placeholder="A topic, product, or community…"></label><div class="actions">${button('✧ Research last 30 days', 'assistant', 'data-kind="research"', 'primary')}${button('Brainstorm post ideas', 'assistant', 'data-kind="ideas"')}</div><p class="small muted">Opens a new Locus chat with an editable request. Send it when you’re ready; results stay in that conversation.</p></div><div class="research-notes">${[['01', 'Listen first', 'The last30days skill looks for recent sources, repeated questions, and emerging discussions.'], ['02', 'Find your angle', 'Your saved audience, voice, and content themes travel with the research request.'], ['03', 'Make it yours', 'Review the evidence, copy a useful angle into a new post, and adapt it for each channel.']].map(([number, title, text]) => `<div><span class="accent-text">${number}</span><h3>${title}</h3><p class="muted">${text}</p></div>`).join('')}</div>`;
}

function activityContent() {
  if (!state.data.connection) return `${empty('Connect your publishing workspace', 'OpenPost handles delivery while Locus keeps your writing and research together.', '↗')}<div class="center">${button('Connect OpenPost', 'connect', '', 'primary')}</div>`;
  return `<div class="row muted small"><span>Latest 100 OpenPost publications</span>${state.data.lastSynced ? `<span>Updated ${e(formatDate(state.data.lastSynced))}</span>` : ''}</div>${state.publications.length ? state.publications.map(post => `<article class="card publication"><div class="row"><h2>${e(post.title || post.sourceText.slice(0, 70) || 'Untitled publication')}</h2>${badge(post.status)}</div><p class="excerpt">${e(post.sourceText)}</p>${dateLabel(post.scheduledAt)}<div class="renditions">${post.renditions.map(rendition => `<div class="destination"><span>${e(CHANNELS[rendition.platform] || rendition.platform)}</span>${badge(rendition.status)}${rendition.errorMessage ? `<span class="error-text small">${e(rendition.errorMessage)}</span>` : ''}${rendition.externalUrl ? button('View post ↗', 'open', `${validID(post.id)} data-target="rendition" data-rendition="${e(rendition.id)}"`, 'link') : ''}</div>`).join('')}</div><div class="row"><div class="actions">${post.status === 'draft' ? `${button('Schedule…', 'review', `${validID(post.id)} data-kind="schedule" ${post.scheduledAt ? '' : 'disabled title="Set a planned time before transfer, or edit the schedule in OpenPost."'}`)}${button('Publish now…', 'review', `${validID(post.id)} data-kind="publish-now"`, 'accent')}` : ''}${post.status === 'scheduled' ? button('Cancel schedule…', 'review', `${validID(post.id)} data-kind="cancel"`) : ''}${button('Review content', 'review', `${validID(post.id)} data-kind="view"`)}</div>${button('Open OpenPost ↗', 'open', `${validID(post.id)} data-target="publication"`, 'link')}</div></article>`).join('') : empty('No publications loaded', 'Send a local draft to OpenPost, or refresh to see existing publications.', '↗')}<p class="muted small">Delivery states come from OpenPost. Engagement analytics, media editing, and inbox tools are available in your OpenPost workspace.</p>`;
}

function accountsContent() {
  const connection = state.data.connection;
  const sources = state.data.legacySources || [];
  return `<div class="card connection-card"><div class="row"><div><h2>${e(connection?.workspaceName || 'Connect OpenPost')}</h2><p class="muted">${e(connection?.origin || 'Use OpenPost Hosted or your own instance.')}</p></div>${badge(connection ? 'Configured' : 'Not connected')}</div><p class="muted">Connect social accounts in OpenPost, then select them when you send a draft. Your developer token is stored in macOS Keychain.</p><div class="actions">${button(connection ? 'Change connection' : 'Connect OpenPost', 'connect', '', 'primary')}${button('Open OpenPost ↗', 'open', 'data-target="openpost"')}${connection ? button('Disconnect', 'disconnect') : ''}</div></div>
  ${state.data.accounts.length ? `<h3 class="eyebrow">CONNECTED DESTINATIONS</h3>${state.data.accounts.map(account => `<div class="card account"><span class="avatar">${e(account.platform === 'linkedin' ? 'in' : (CHANNELS[account.platform] || account.platform).slice(0, 1))}</span><strong>${e(accountLabel(account))}</strong>${badge(account.isActive ? 'Active' : 'Reconnect in OpenPost')}</div>`).join('')}` : ''}<p class="muted small">Platform capabilities vary. OpenPost validates each destination before scheduling or publishing. Use its editors for images, carousels, video, and media-required channels.</p>
  <div class="card import-card"><h2>Bring your earlier drafts</h2><p class="muted">Import Social Studio data previously saved by Locus or LocusX for this project. Drafts, variants, brand voice, and pending transfers keep their original identities. Your original files and Keychain entries stay in place.</p><p class="small muted">Import requires an empty studio. Reconnect OpenPost after importing. A pending transfer must reconnect to its original instance and workspace before retrying.</p><div class="actions">${['Locus', 'LocusX'].map(edition => button(`Import from ${edition}…`, 'import-review', `data-edition="${edition}"`)).join('')}</div>${sources.length ? `<p class="small muted">Found: ${sources.map(source => e(typeof source === 'string' ? source : source.edition || source.name)).join(', ')}</p>` : '<p class="small muted">Choose an edition to check for saved data for this project.</p>'}</div>`;
}

function brandContent() {
  return `<form id="brand-form" class="brand-form"><p class="muted">A shared starting point for your writing and research.</p>${[['name', 'Brand or creator name', 'How you introduce yourself'], ['audience', 'Audience', 'Who you’re writing for'], ['voice', 'Voice', 'Tone, phrases you use, and things to avoid'], ['topics', 'Content themes', 'The topics you want to be known for']].map(([field, label, placeholder]) => `<label class="field"><span>${label}</span><textarea name="${field}" rows="${field === 'voice' ? 4 : 2}" placeholder="${placeholder}">${e(state.data.brand[field] || '')}</textarea></label>`).join('')}<div>${button('Save brand voice', 'save-brand', '', 'primary')}</div><p class="muted small">Stored for this Locus project and included in assistant requests you open from Social Studio.</p></form>`;
}

function showSheet(kind, content, wide = false) {
  state.sheet = kind;
  modal.classList.toggle('wide', wide);
  modal.innerHTML = content;
  if (!modal.open) modal.showModal();
  if (state.busy) setBusy(true);
}
const sheetError = '<p class="sheet-error error-text" role="alert" hidden></p>';
function closeSheet() { modal.close(); state.sheet = null; state.editor = null; }

function openEditor(draft) {
  state.editor = {base: structuredClone(draft), draft: structuredClone(draft), channel: null};
  renderEditor();
}

async function completeDraft(draft) {
  if (!draft.variantsOmitted) return draft;
  for (const channel of draft.variantChannels || []) {
    const result = await bridge.call('draft_get', {id: draft.id, field: `variant:${channel}`});
    draft.variants[channel] = result.value;
  }
  delete draft.variantsOmitted;
  delete draft.variantChannels;
  return draft;
}

function renderEditor() {
  const editor = state.editor;
  const draft = editor.draft;
  showSheet('editor', `<div class="sheet-header"><div><h2 id="modal-title">Compose a post</h2><p class="muted">One idea. A version for every channel.</p></div><div class="actions">${button('Cancel', 'editor-cancel')}${button('Save draft', 'save-draft', '', 'primary')}</div></div><div class="composer"><div class="composer-fields"><label class="sr-only" for="draft-title">Post title</label><input id="draft-title" class="title-input" placeholder="Give this post a title" value="${e(draft.title)}"><div class="tabs" role="tablist" aria-label="Post versions">${[null, ...draft.channels].map(channel => button(e(channel ? CHANNELS[channel] : 'Original'), 'editor-tab', `data-channel="${channel || ''}" role="tab" aria-selected="${editor.channel === channel}"`, editor.channel === channel ? 'active' : '')).join('')}</div><label class="sr-only" for="draft-text">${e(editor.channel ? CHANNELS[editor.channel] + ' version' : 'Original post')}</label><textarea id="draft-text" class="draft-text" placeholder="Start with something worth sharing…">${e(textForChannel(draft, editor.channel))}</textarea><div class="row wrap"><span id="character-count" class="muted small">${[...textForChannel(draft, editor.channel)].length} characters</span><div class="actions">${editor.channel && Object.hasOwn(draft.variants || {}, editor.channel) ? button('Use original', 'original') : ''}${button('✧ Adapt with Locus', 'adapt', '', 'link')}</div></div><hr><fieldset><legend class="eyebrow">CHANNELS</legend><div class="channel-grid">${Object.entries(CHANNELS).map(([key, label]) => `<label class="checkbox"><input type="checkbox" data-channel="${key}" ${draft.channels.includes(key) ? 'checked' : ''}>${label}</label>`).join('')}</div></fieldset><label class="checkbox"><input id="plan-enabled" type="checkbox" ${draft.plannedAt ? 'checked' : ''}>Plan a date</label>${draft.plannedAt ? `<label class="field"><span>Planned for</span><input id="planned-at" type="datetime-local" value="${e(localDateTime(draft.plannedAt))}"></label><p class="small muted">${e(timezone())} · Saved as a local plan until scheduled through OpenPost.</p>` : ''}${sheetError}</div><aside class="preview"><span class="eyebrow">TEXT PREVIEW</span><div class="card preview-card"><div class="preview-person"><span class="avatar round">${e((state.data.brand.name || 'S').slice(0, 1).toUpperCase())}</span><div><strong>${e(state.data.brand.name || 'Your brand')}</strong><span>${e(editor.channel ? CHANNELS[editor.channel] : 'Original post')}</span></div></div><p id="preview-text" class="preserve">${e(textForChannel(draft, editor.channel) || 'Your words will appear here.')}</p><hr><span class="preview-icons" aria-hidden="true">♡ &nbsp; ◯ &nbsp; ↗</span></div><p class="muted small">A text preview, not an exact platform rendering. OpenPost checks destination limits and media requirements before publishing.</p></aside></div>`, true);
}

function captureEditor({validateDate = false} = {}) {
  if (!state.editor) return;
  const editor = state.editor;
  const title = modal.querySelector('#draft-title');
  const text = modal.querySelector('#draft-text');
  if (title) editor.draft.title = title.value;
  if (text) {
    if (editor.channel) {
      if (Object.hasOwn(editor.draft.variants, editor.channel) || text.value !== editor.draft.text) editor.draft.variants[editor.channel] = text.value;
    } else editor.draft.text = text.value;
  }
  const date = modal.querySelector('#planned-at');
  if (date) {
    if (!date.value || Number.isNaN(+new Date(date.value))) {
      if (validateDate) throw new Error('Choose a valid local planned date and time.');
    } else editor.draft.plannedAt = new Date(date.value).toISOString();
  }
}

async function saveEditor() {
  captureEditor({validateDate: true});
  const editor = state.editor;
  if (!editor.draft.id) {
    const result = await bridge.call('draft_create');
    editor.base = await completeDraft(result.draft);
    editor.draft.id = result.draft.id;
  }
  // Each field fits one bounded bridge request. Keep the latest saved base if
  // a later field fails, so the open editor can safely resume the save.
  for (const change of draftChanges(editor.base, editor.draft)) {
    const result = await bridge.call('draft_update', {id: editor.draft.id, ...change});
    // A large response may omit variants. The exact field we just saved is
    // already in the editor, so update the known base without resending data.
    if (result.draft.variantsOmitted) {
      if (change.field.startsWith('variant:')) {
        if (change.value === null) delete editor.base.variants[change.field.slice(8)];
        else editor.base.variants[change.field.slice(8)] = change.value;
      } else editor.base[change.field] = structuredClone(change.value);
    } else editor.base = result.draft;
  }
  return editor.draft.id;
}

function connectSheet() {
  showSheet('connection', `<div class="sheet-body"><h2 id="modal-title">Connect OpenPost</h2><p class="muted">Use a developer token from OpenPost → Settings → Personal → Developer. Allow API read and write access to your workspace.</p><label class="field"><span>Instance address</span><input id="origin" type="url" value="${e(state.data.connection?.origin || 'https://app.openpo.st')}" placeholder="https://app.openpo.st"></label><p class="small muted">Self-hosted? Enter your instance’s origin without /api/v1.</p><p class="security-note">Your token is entered in a native macOS password dialog and stored in Keychain. It never enters this page or the chat.</p><div id="workspace-picker"></div>${sheetError}<div class="sheet-footer">${button('Cancel', 'close')}<div class="actions">${button('Developer tokens ↗', 'open', 'data-target="developer-tokens"', 'link')}${button('Enter token & find workspaces', 'find-workspaces', '', 'primary')}</div></div></div>`);
}

async function transferSheet(id) {
  if (!state.data.connection) { connectSheet(); return; }
  const {draft} = await bridge.call('draft_get', {id});
  if (publicationID(draft)) { state.section = 'activity'; await reload(); return; }
  state.transfer = draft;
  showSheet('transfer', `<div class="sheet-body"><h2 id="modal-title">${draft.handoff ? 'Retry draft transfer' : 'Send draft to OpenPost'}</h2><h3>${e(displayTitle(draft))}</h3><p class="muted">Workspace: ${e(state.data.connection.workspaceName)}</p>${draft.handoff ? '<p>Retries the exact saved content and destinations with the original transfer key. This avoids creating another draft if the first response was lost.</p>' : `<p class="muted">Choose destinations. Each receives its platform version, or the original if you haven’t written one.</p><div class="destination-picker">${state.data.accounts.map(account => `<label class="checkbox"><input type="checkbox" name="destination" value="${e(account.id)}" ${account.isActive && draft.channels.includes(account.platform) ? 'checked' : ''} ${account.isActive ? '' : 'disabled'}>${e(accountLabel(account))}${account.isActive ? '' : ' · inactive'}</label>`).join('') || '<p class="muted">No destinations loaded. Refresh Accounts to check your connections.</p>'}</div><p class="small muted">You can leave destinations empty and add them in OpenPost later.</p>`}<p class="security-note">Creates an unpublished draft. Schedule or publish separately in Activity. Once sent, this local draft is frozen; duplicate it to keep editing.</p>${sheetError}<div class="sheet-footer">${button('Cancel', 'close')}${button(draft.handoff ? 'Retry saved transfer' : 'Send draft', 'send-draft', validID(draft.id), 'primary')}</div></div>`);
}

async function reviewSheet(id, action) {
  const result = await bridge.call('publication_get', {id});
  const post = normalizePublication(result.publication);
  state.review = {post, action};
  const title = {schedule: 'Schedule publication', 'publish-now': 'Publish now', cancel: 'Cancel schedule', view: 'Publication content'}[action];
  showSheet('review', `<div class="sheet-body"><div class="row"><h2 id="modal-title">${title}</h2>${badge(post.status)}</div><h3>${e(post.title || post.sourceText.slice(0, 70) || 'Untitled publication')}</h3><div class="review-content"><p class="preserve">${e(post.sourceText)}</p>${post.renditions.map(rendition => `<div class="card review-rendition"><strong>${e(accountLabel(state.data.accounts.find(account => account.id === rendition.socialAccountId) || {platform: rendition.platform, accountUsername: 'destination'}))}</strong><p class="preserve">${e(rendition.body ?? post.sourceText)}</p>${badge(rendition.status)}${rendition.errorMessage ? `<p class="error-text">${e(rendition.errorMessage)}</p>` : ''}</div>`).join('')}</div>${post.scheduledAt ? `<p class="muted">◷ ${e(formatDate(post.scheduledAt))} · ${e(timezone())}</p>` : ''}<p class="muted small">${action === 'publish-now' ? 'OpenPost will publish this saved revision to its selected destinations. Check every destination version before continuing.' : action === 'schedule' ? 'OpenPost will run this saved schedule even when Locus is closed.' : action === 'cancel' ? 'OpenPost will cancel the scheduled job. A post that has already started publishing may still complete.' : 'This is the current saved content in OpenPost.'}</p><p class="muted small">Reviewed revision: ${e(post.revision)}. If it changes, reload and review again.</p>${sheetError}<div class="sheet-footer">${button('Back', 'close')}<div class="actions">${button('Reload review', 'review', `${validID(id)} data-kind="${action}"`)}${action !== 'view' ? button(title, 'publication-action', !Number.isInteger(post.revision) ? 'disabled' : '', action === 'publish-now' ? 'primary' : 'accent') : ''}</div></div></div>`);
}

async function handleAction(target) {
  const {action, id, kind} = target.dataset;
  switch (action) {
    case 'dismiss-message': state.message = null; document.querySelector('#banner').innerHTML = ''; break;
    case 'section': state.section = target.dataset.section; await reload(); break;
    case 'refresh': await reload({remote: true}); notify('Updated from OpenPost.'); break;
    case 'page': state.offset = Number(target.dataset.offset); await reload(); break;
    case 'new': openEditor({title: '', text: '', channels: ['linkedin'], variants: {}, plannedAt: null}); break;
    case 'starter': {
      const starters = {update: ['A product update', 'What we shipped:\n\nWhy it matters:\n\nTry it:'], lesson: ['Something you learned', 'Something I learned this week:\n\nWhat surprised me:\n\nWhat I’d do differently:'], conversation: ['Start a conversation', 'I’ve been thinking about…\n\nMy experience so far:\n\nHow do you approach this?']};
      const [title, text] = starters[target.dataset.starter]; openEditor({title, text, channels: ['linkedin'], variants: {}, plannedAt: null}); break;
    }
    case 'edit': { const {draft} = await bridge.call('draft_get', {id}); if (draft.handoff) throw new Error('This draft has a saved transfer. Duplicate it to edit a new local copy.'); openEditor(await completeDraft(draft)); break; }
    case 'duplicate': { const {draft} = await bridge.call('draft_duplicate', {id}); await loadState(); render(); openEditor(await completeDraft(draft)); notify('Created a new editable copy.'); break; }
    case 'copy': await bridge.call('copy', {id}); notify('Original post copied to the clipboard.'); break;
    case 'export': await bridge.call('export'); notify('Export dialog finished.'); break;
    case 'remove': state.removing = id; showSheet('remove', `<div class="sheet-body"><h2 id="modal-title">Remove local draft?</h2><p>This removes only the local copy. Any draft or scheduled post already in OpenPost stays there.</p>${sheetError}<div class="sheet-footer">${button('Cancel', 'close')}${button('Remove local draft', 'remove-confirm', '', 'danger')}</div></div>`); break;
    case 'remove-confirm': await bridge.call('draft_delete', {id: state.removing}); closeSheet(); state.offset = 0; await reload(); notify('Local draft removed.'); break;
    case 'close': closeSheet(); break;
    case 'editor-cancel': {
      captureEditor();
      if (draftChanges(state.editor.base, state.editor.draft).length) {
        showSheet('discard', `<div class="sheet-body"><h2 id="modal-title">Discard unsaved changes?</h2><p>Any fields already saved successfully will remain in your local draft.</p><div class="sheet-footer">${button('Keep editing', 'keep-editing')}${button('Discard changes', 'close', '', 'danger')}</div></div>`);
      } else closeSheet();
      break;
    }
    case 'keep-editing': renderEditor(); break;
    case 'editor-tab': captureEditor(); state.editor.channel = target.dataset.channel || null; renderEditor(); break;
    case 'original': captureEditor(); delete state.editor.draft.variants[state.editor.channel]; renderEditor(); break;
    case 'save-draft': await saveEditor(); closeSheet(); await reload(); notify('Draft saved on this Mac.'); break;
    case 'adapt': {
      captureEditor();
      if (!state.editor.draft.text.trim() || !state.editor.draft.channels.length) throw new Error('Write an original post and choose at least one channel first.');
      const draftID = await saveEditor();
      const result = await bridge.call('assistant_prompt', {action: 'adapt', id: draftID});
      await bridge.compose(result.prompt); notify('Draft saved. An editable adaptation request opened in Locus. Send it when you’re ready.'); break;
    }
    case 'plan': openEditor({title: '', text: '', channels: ['linkedin'], variants: {}, plannedAt: new Date(`${target.dataset.date}T09:00`).toISOString()}); break;
    case 'month': { const delta = Number(target.dataset.delta); state.month = delta ? new Date(state.month.getFullYear(), state.month.getMonth() + delta, 1) : new Date(); await loadCalendar(); render(); break; }
    case 'assistant': {
      const topic = document.querySelector('#topic').value.trim(); if (!topic) throw new Error('Enter a topic, product, or community first.');
      const result = await bridge.call('assistant_prompt', {action: kind, topic});
      await bridge.compose(result.prompt); notify('An editable request opened in Locus. Send it when you’re ready.'); break;
    }
    case 'save-brand': {
      const values = Object.fromEntries([...document.querySelectorAll('#brand-form [name]')].map(input => [input.name, input.value]));
      for (const [field, value] of Object.entries(values)) if (value !== state.data.brand[field]) { const result = await bridge.call('brand_update', {field, value}); state.data.brand = result.brand; }
      notify('Brand voice saved for this project.'); break;
    }
    case 'connect': connectSheet(); break;
    case 'find-workspaces': {
      const origin = modal.querySelector('#origin').value.trim();
      const result = await bridge.call('connect', {origin});
      if (!result.workspaces?.length) throw new Error('This token has no accessible workspaces. Check its workspace access in OpenPost.');
      modal.querySelector('#origin').readOnly = true;
      modal.querySelector('#workspace-picker').innerHTML = `<label class="field"><span>Workspace</span><select id="connection-workspace">${result.workspaces.map(workspace => `<option value="${e(workspace.id)}">${e(workspace.name)}${(workspace.can_edit ?? workspace.canEdit) ? '' : ' · read only'}</option>`).join('')}</select></label>`;
      target.dataset.action = 'connect-workspace'; target.textContent = 'Connect workspace'; break;
    }
    case 'connect-workspace': await bridge.call('connect_workspace', {workspaceId: modal.querySelector('#connection-workspace').value}); closeSheet(); await loadState(); await reload({remote: true}); notify('Connected to OpenPost.'); break;
    case 'disconnect': showSheet('disconnect', `<div class="sheet-body"><h2 id="modal-title">Disconnect OpenPost?</h2><p>Your local drafts stay here. Existing scheduled jobs in OpenPost will continue.</p>${sheetError}<div class="sheet-footer">${button('Cancel', 'close')}${button('Disconnect', 'disconnect-confirm', '', 'danger')}</div></div>`); break;
    case 'disconnect-confirm': await bridge.call('disconnect'); closeSheet(); await reload(); notify('OpenPost disconnected.'); break;
    case 'transfer': await transferSheet(id); break;
    case 'send-draft': {
      const accountIds = [...modal.querySelectorAll('input[name="destination"]:checked')].map(input => input.value);
      const result = await bridge.call('send_draft', {id, accountIds}); closeSheet(); await reload(); notify(result.notice || 'Draft sent to OpenPost. Review it in Activity before scheduling or publishing.'); break;
    }
    case 'review': await reviewSheet(id, kind); break;
    case 'publication-action': {
      const {post, action: operation} = state.review;
      const result = await bridge.call('publication_action', {id: post.id, revision: post.revision, action: operation});
      closeSheet(); await reload(); notify(result.notice || 'OpenPost accepted the request. Check the delivery state in Activity.'); break;
    }
    case 'open': await bridge.call('open', {target: target.dataset.target, ...(id ? {id} : {}), ...(target.dataset.rendition ? {renditionId: target.dataset.rendition} : {})}); break;
    case 'import-review': state.importEdition = target.dataset.edition; showSheet('import', `<div class="sheet-body"><h2 id="modal-title">Import from ${e(state.importEdition)}?</h2><p>Import this project’s earlier drafts, brand settings, and saved transfer envelopes. Existing original files and Keychain credentials are left untouched.</p><p>After import, reconnect OpenPost. A pending transfer requires its original instance and workspace.</p>${sheetError}<div class="sheet-footer">${button('Cancel', 'close')}${button('Import project data', 'import-confirm', '', 'primary')}</div></div>`); break;
    case 'import-confirm': { const result = await bridge.call('import_legacy', {edition: state.importEdition}); closeSheet(); await reload(); notify(result.notice || 'Imported project data. Reconnect OpenPost before transferring or publishing.'); break; }
  }
}

document.addEventListener('click', event => {
  const target = event.target.closest('button[data-action]');
  if (target && !target.disabled) void perform(() => handleAction(target));
});
document.addEventListener('submit', event => event.preventDefault());
document.addEventListener('input', event => {
  if (event.target.id === 'search') {
    state.search = event.target.value; state.offset = 0;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      try { await loadState(); if (state.section === 'drafts') document.querySelector('#content').innerHTML = draftContent(); }
      catch (error) { notify(error.message, true); }
    }, 180);
  }
  if (event.target.id === 'draft-text') {
    modal.querySelector('#character-count').textContent = `${[...event.target.value].length} characters`;
    modal.querySelector('#preview-text').textContent = event.target.value || 'Your words will appear here.';
  }
});
document.addEventListener('change', event => {
  if (state.sheet !== 'editor' || !(event.target.matches('input[type="checkbox"][data-channel]') || event.target.id === 'plan-enabled')) return;
  void perform(async () => {
    const input = event.target;
    if (input.matches('input[type="checkbox"][data-channel]')) {
      captureEditor();
      const channel = input.dataset.channel;
      state.editor.draft.channels = input.checked ? [...new Set([...state.editor.draft.channels, channel])] : state.editor.draft.channels.filter(value => value !== channel);
      if (!input.checked && state.editor.channel === channel) state.editor.channel = null;
      renderEditor();
    }
    if (input.id === 'plan-enabled') {
      captureEditor(); state.editor.draft.plannedAt = input.checked ? new Date(Date.now() + 86400000).toISOString() : null; renderEditor();
    }
  });
});
modal.addEventListener('cancel', event => {
  event.preventDefault();
  if (!state.busy) void perform(() => state.sheet === 'editor' ? handleAction({dataset: {action: 'editor-cancel'}}) : Promise.resolve(closeSheet()));
});
document.addEventListener('keydown', event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's' && state.sheet === 'editor') { event.preventDefault(); void perform(() => handleAction({dataset: {action: 'save-draft'}})); }
  if ((event.metaKey || event.ctrlKey) && event.altKey && event.key.toLowerCase() === 'n' && !modal.open) { event.preventDefault(); void perform(() => handleAction({dataset: {action: 'new'}})); }
});
window.addEventListener('pagehide', () => bridge.dispose());

try {
  context = await bridge.start();
  await loadState();
  render();
  if (state.data.connection) await perform(async () => { await bridge.call('refresh'); await loadState(); render(); });
} catch (error) {
  app.innerHTML = `<main class="startup"><span class="brand-mark" aria-hidden="true">S</span><h1>Social Studio</h1><p role="alert">${e(error.message)}</p></main>`;
}
