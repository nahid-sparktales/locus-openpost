export const MAX_ARGUMENT_BYTES = 256 * 1024;
export const MAX_COMPOSE_CHARACTERS = 16000;

export function checkedArguments(operation, payload = {}) {
  const args = {operation, payload};
  if (new TextEncoder().encode(JSON.stringify(args)).byteLength > MAX_ARGUMENT_BYTES) {
    throw new Error('This change is too large for Locus’s panel bridge (256 KiB). Shorten the field, or export your draft before splitting it. Nothing was sent.');
  }
  return args;
}

export function checkedPrompt(text) {
  // Counting code points is stricter than Swift's grapheme count, so any
  // accepted request also fits the native bridge. Never silently truncate.
  if (typeof text !== 'string' || !text.trim()) throw new Error('The assistant request is empty.');
  if ([...text].length > MAX_COMPOSE_CHARACTERS) {
    throw new Error('This assistant request exceeds 16,000 characters. Shorten your draft or brand context and try again. Your text has not been changed.');
  }
  return text;
}

export function unpackResult(result) {
  if (result?.is_error || result?.isError) {
    const content = typeof result.content === 'string' ? result.content : result.content?.map(item => item.text || '').join('\n');
    throw new Error(content || 'Social Studio could not complete this request.');
  }
  if (typeof result?.content === 'string') {
    try { return JSON.parse(result.content); }
    catch { throw new Error('Locus returned an incomplete response. Update Locus and reopen Social Studio. No action was retried.'); }
  }
  if (Array.isArray(result?.content)) {
    try { return JSON.parse(result.content.filter(item => item.type === 'text').map(item => item.text).join('\n')); }
    catch { throw new Error('Social Studio returned an unreadable response. No action was retried.'); }
  }
  if (result && typeof result === 'object') return result;
  throw new Error('Social Studio returned an empty response. No action was retried.');
}

export class PanelBridge {
  constructor(host = window, timeout = 120000) {
    this.host = host;
    this.timeout = timeout;
    this.pending = new Map();
    this.sequence = 0;
    this.context = null;
    this.ready = new Promise((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
    host.locusPanel = {receive: message => this.receive(message)};
  }

  start() {
    if (!this.host.webkit?.messageHandlers?.locusPanel?.postMessage) {
      this.rejectReady(new Error('Open Social Studio from Locus → Work after installing this plugin. This page needs Locus’s project permissions and native tools.'));
    } else {
      this.helloTimer = setTimeout(() => this.rejectReady(new Error('Locus did not open a project connection. Close this window and reopen Social Studio from Work.')), 15000);
      this.post({version: 1, type: 'ready'});
    }
    return this.ready;
  }

  post(message) { this.host.webkit.messageHandlers.locusPanel.postMessage(message); }

  receive(message) {
    if (!message || message.version !== 1) return;
    if (message.type === 'hello') {
      clearTimeout(this.helloTimer);
      if (message.toolContextVersion !== 1 || !message.workspace?.startsWith('/') || !message.capabilities?.includes('plugin.tools')) {
        this.rejectReady(new Error('Update Locus to a version with project-bound plugin tools, then reopen Social Studio. This version cannot safely connect publishing tools to your project.'));
        return;
      }
      if (this.context && (message.workspace !== this.context.workspace || message.panel !== this.context.panel)) {
        this.dispose('The panel’s project changed. Close and reopen Social Studio. No action was retried.');
        return;
      }
      this.context = Object.freeze({...message});
      this.resolveReady(this.context);
    }
    if (message.type === 'response') {
      const pending = this.pending.get(message.requestID);
      if (!pending) return;
      this.pending.delete(message.requestID);
      clearTimeout(pending.timer);
      if (message.ok) {
        try { pending.resolve(unpackResult(message.result)); } catch (error) { pending.reject(error); }
      } else pending.reject(new Error(message.error || 'Locus refused this request. Reopen the plugin to check its permissions.'));
    }
  }

  async call(operation, payload = {}) {
    await this.ready;
    if (!this.context) throw new Error('This panel is no longer connected. Reopen it from Locus → Work.');
    const args = checkedArguments(operation, payload);
    const requestID = `studio-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestID);
        reject(new Error('The request has not returned. Its outcome is unknown; nothing was retried. Refresh to check the saved state before trying again. A pending transfer can be retried with its original saved key.'));
      }, this.timeout);
      this.pending.set(requestID, {resolve, reject, timer});
      try { this.post({version: 1, type: 'callTool', requestID, tool: 'social_studio', arguments: args}); }
      catch (error) { clearTimeout(timer); this.pending.delete(requestID); reject(error); }
    });
  }

  async compose(text) {
    await this.ready;
    if (!this.context?.capabilities?.includes('chat.compose')) throw new Error('This panel does not have permission to open an editable Locus chat.');
    this.post({version: 1, type: 'composeChat', text: checkedPrompt(text)});
  }

  dispose(reason = 'The panel closed. No action was retried.') {
    this.context = null;
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(new Error(reason)); }
    this.pending.clear();
  }
}
