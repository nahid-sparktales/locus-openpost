export const CHANNELS = {linkedin: 'LinkedIn', x: 'X', bluesky: 'Bluesky', threads: 'Threads', mastodon: 'Mastodon', instagram: 'Instagram', facebook: 'Facebook', tiktok: 'TikTok', youtube: 'YouTube', pinterest: 'Pinterest'};
export const SECTIONS = {
  drafts: ['Drafts', 'A good post starts with something worth sharing.', '✎'],
  calendar: ['Calendar', 'Give your ideas a place in the week.', '▦'],
  research: ['Research', 'Find the conversations worth joining.', '✧'],
  activity: ['Activity', 'Follow every post from draft to delivery.', '↗'],
  accounts: ['Accounts', 'Your publishing destinations, together.', '◎'],
  brand: ['Brand voice', 'Keep every channel sounding like you.', '≋'],
};
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
export const displayTitle = value => value?.title?.trim() || 'Untitled post';
export const textForChannel = (draft, channel) => channel ? draft.variants?.[channel] ?? draft.text ?? '' : draft.text ?? '';
export const publicationID = draft => draft?.handoff?.publicationID ?? draft?.handoff?.publicationId;
export const draftStatus = draft => publicationID(draft) ? 'In OpenPost' : draft.handoff ? 'Transfer pending' : draft.plannedAt ? 'Planned locally' : 'Draft';
export const timezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
export function formatDate(value, withTime = true) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(+date) ? 'Invalid date' : date.toLocaleString(undefined, {month: 'short', day: 'numeric', year: 'numeric', ...(withTime ? {hour: 'numeric', minute: '2-digit'} : {})});
}
export function dateKey(date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; }
export function localDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  return `${dateKey(date)}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}
export function monthDays(month, firstWeekday = 0) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const offset = (first.getDay() - firstWeekday + 7) % 7;
  return Array.from({length: 42}, (_, index) => new Date(first.getFullYear(), first.getMonth(), 1 - offset + index));
}
export function normalizeAccount(value) {
  return {...value, accountUsername: value.accountUsername ?? value.account_username ?? '', isActive: value.isActive ?? value.is_active ?? false};
}
export function accountLabel(value) { return `${CHANNELS[value.platform] || value.platform} · @${value.accountUsername}`; }
export function normalizePublication(value) {
  return {...value, sourceText: value.sourceText ?? value.source_text ?? '', scheduledAt: value.scheduledAt ?? value.scheduled_at,
    renditions: (value.renditions || []).map(item => ({...item, errorMessage: item.errorMessage ?? item.error_message, externalUrl: item.externalUrl ?? item.external_url, socialAccountId: item.socialAccountId ?? item.social_account_id}))};
}
export function draftChanges(base, draft) {
  const result = [];
  for (const field of ['title', 'text', 'channels', 'plannedAt']) {
    const value = draft[field] ?? (field === 'plannedAt' ? null : field === 'channels' ? [] : '');
    if (JSON.stringify(base[field] ?? (field === 'plannedAt' ? null : field === 'channels' ? [] : '')) !== JSON.stringify(value)) result.push({field, value});
  }
  for (const channel of new Set([...Object.keys(base.variants || {}), ...Object.keys(draft.variants || {})])) {
    if (base.variants?.[channel] !== draft.variants?.[channel]) result.push({field: `variant:${channel}`, value: draft.variants?.[channel] ?? null});
  }
  return result;
}
