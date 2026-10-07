// SPDX-License-Identifier: Apache-2.0
export const profileContexts = Object.freeze({
  'qwen3.8-flash-next-lily-q4-64k': 65536,
  'qwen3.8-flash-next-lily-q4-128k': 131072,
  'qwen3.8-flash-next-lily-q4-262k': 262144,
});
const states = new Set(['disabled', 'queued', 'ready', 'unloaded', 'loading', 'active', 'cleanup']);
/** @param {any} value */
export function parseProfileStatus(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.profiles) || value.profiles.length !== 3
      || !Number.isInteger(value.pending_count) || value.pending_count < 0 || value.pending_count > 4
      || typeof value.admission_paused !== 'boolean') throw new Error('Invalid profile status');
  const seen = new Set();
  const profiles = value.profiles.map(row => {
    if (!row || !Object.hasOwn(profileContexts, row.model) || seen.has(row.model)
        || row.context_tokens !== profileContexts[row.model] || typeof row.enabled !== 'boolean'
        || typeof row.loaded !== 'boolean' || !states.has(row.state)
        || !Number.isInteger(row.queued) || row.queued < 0 || row.queued > 4) throw new Error('Invalid profile status');
    seen.add(row.model);
    return Object.freeze({model: row.model, context: row.context_tokens, enabled: row.enabled,
                          loaded: row.loaded, state: row.state, queued: row.queued});
  });
  return Object.freeze({profiles: Object.freeze(profiles), pending: value.pending_count, paused: value.admission_paused});
}

/** @param {Response} response */
export async function readProfileStatus(response) {
  if (!response.ok || !response.body) throw new Error('Profile status unavailable');
  const reader = response.body.getReader();
  const chunks = []; let bytes = 0;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 16 * 1024) throw new Error('Profile status too large');
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel(); throw error;
  } finally { reader.releaseLock(); }
  const body = new Uint8Array(bytes); let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  return parseProfileStatus(JSON.parse(new TextDecoder().decode(body)));
}
