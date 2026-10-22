// Small DOM, formatting and dialog helpers shared by every view.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** h('div.card', {onclick}, child, 'text') -> element. Attributes starting with "on" bind events. */
export function h(tag, attrs = {}, ...kids) {
  const [name, ...classes] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (classes.length) el.className = classes.join(' ');
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'html') el.innerHTML = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/** replaceChildren that skips null/false (conditional children), like h() does. */
export function fill(el, ...kids) {
  el.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
  return el;
}

export function toast(msg, kind = '') {
  const el = h(`div.toast${kind ? '.' + kind : ''}`, { role: 'status' }, msg);
  $('#toasts').append(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 9000 : 4500);
}

const modal = () => $('#modal');

export function openModal(...content) {
  const body = $('#modal-body');
  body.replaceChildren(...content);
  modal().showModal();
  return modal();
}

export function closeModal() { modal().close(); }

export function confirmDialog(title, text, okLabel = 'Delete', danger = true) {
  return new Promise((resolve) => {
    const done = (v) => { closeModal(); resolve(v); };
    openModal(
      h('h3', {}, title), h('p', {}, text),
      h('div.modal-actions', {},
        h('button.btn.ghost', { type: 'button', onclick: () => done(false) }, 'Cancel'),
        h(`button.btn${danger ? '.danger' : '.primary'}`, { type: 'button', onclick: () => done(true) }, okLabel)));
    modal().addEventListener('close', () => resolve(false), { once: true });
  });
}

export function fmtBytes(n) {
  if (n == null) return '-';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(n < 10 && i ? 1 : 0)} ${u[i]}`;
}

export function fmtCount(n) {
  if (n == null) return '-';
  return n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : String(n);
}

export function fmtDuration(s) {
  if (s == null || !isFinite(s)) return '';
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function fmtDate(t) {
  const d = new Date(t * 1000);
  const today = new Date();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === today.toDateString() ? `Today ${time}` : `${d.toLocaleDateString()} ${time}`;
}

/** Remember small UI preferences per browser; storage can be unavailable (private mode). */
export const prefs = {
  get(key, fallback) {
    try { const v = localStorage.getItem('atelier.' + key); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem('atelier.' + key, JSON.stringify(value)); } catch { /* ignore */ } },
};

/** Make a .segmented group behave like radio buttons. Returns {get, set}. */
export function segmented(el, onChange, initial) {
  const buttons = $$('button', el);
  const set = (v, fire = false) => {
    for (const b of buttons) {
      const on = b.dataset.v === String(v);
      b.classList.toggle('on', on);
      b.setAttribute('aria-checked', on);
    }
    if (fire) onChange?.(v);
  };
  for (const b of buttons) {
    b.type = 'button';
    b.setAttribute('role', 'radio');
    b.addEventListener('click', () => { if (!b.disabled) set(b.dataset.v, true); });
  }
  if (initial !== undefined) set(initial);
  return { set, get: () => buttons.find((b) => b.classList.contains('on'))?.dataset.v };
}
