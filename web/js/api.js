// Thin client for the Atelier3D REST API and its live event stream.

export async function api(path, { method = 'GET', json, form, body, headers } = {}) {
  const opts = { method, headers: { ...(headers || {}) } };
  if (json !== undefined) {
    opts.body = JSON.stringify(json);
    opts.headers['Content-Type'] = 'application/json';
  } else if (form) {
    opts.body = form;
  } else if (body !== undefined) {
    opts.body = body;
  }
  const r = await fetch(path, opts);
  const type = r.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await r.json() : await r.text();
  if (!r.ok) {
    const detail = typeof data === 'object' ? data.detail : data;
    throw new Error(Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : detail || r.statusText);
  }
  return data;
}

/** Subscribe to /api/events. The browser reconnects on its own; onState reports the link. */
export function subscribe(onEvent, onState) {
  const es = new EventSource('/api/events');
  es.onmessage = (m) => {
    try { onEvent(JSON.parse(m.data)); } catch (e) { console.error('bad event', e); }
  };
  es.onopen = () => onState?.(true);
  es.onerror = () => onState?.(false);
  return es;
}
