// Library and Models pages.
import { $, h, fill, toast, segmented, fmtBytes, fmtCount, fmtDate, confirmDialog, openModal, prefs } from './ui.js';

let ctx;
let filter;
const packState = new Map();   // live download progress by pack id

export function initPages(c) {
  ctx = c;
  filter = segmented($('#lib-filter'), (v) => { prefs.set('libFilter', v); renderLibrary(); }, prefs.get('libFilter', 'all'));
  let t;
  $('#lib-search').addEventListener('input', () => { clearTimeout(t); t = setTimeout(renderLibrary, 150); });
  $('#link-models').addEventListener('click', linkModels);
}

// ---------------------------------------------------------------- library

export function renderLibrary() {
  const q = $('#lib-search').value.trim().toLowerCase();
  const f = filter.get();
  const items = ctx.S.assets.filter((a) => {
    if (q && !a.name.toLowerCase().includes(q)) return false;
    const mode = (a.source || a.params?.source || {}).mode;
    if (f === 'fav') return a.favorite;
    if (f === 'text') return mode === 'text';
    if (f === 'image') return a.kind === 'image3d' && mode !== 'text';
    if (f === 'multiview') return a.kind === 'multiview3d';
    return true;
  });
  $('#lib-empty').hidden = items.length > 0;
  $('#lib-grid').replaceChildren(...items.map((a) => h('div.card.lib-card', {
    role: 'button', tabindex: 0, title: `Open ${a.name}`,
    onclick: () => { ctx.goCreate(); ctx.selectAsset(a); },
    onkeydown: (e) => { if (e.key === 'Enter') { ctx.goCreate(); ctx.selectAsset(a); } },
  },
  h('div.img', {}, h('img', { src: a.thumb, alt: '', loading: 'lazy' }),
    h(`button.icon-btn.star.fav${a.favorite ? '.on' : ''}`, {
      type: 'button', 'aria-label': 'Favourite', 'aria-pressed': a.favorite,
      onclick: async (e) => {
        e.stopPropagation();
        Object.assign(a, await ctx.api(`/api/assets/${a.id}`, { method: 'PATCH', json: { favorite: !a.favorite } }));
        renderLibrary();
      },
    }, a.favorite ? '★' : '☆')),
  h('div.meta', {}, h('b', {}, a.name), h('span', {}, `${fmtDate(a.created)} · ${fmtCount(a.stats?.faces)} tris`)))));
}

// ---------------------------------------------------------------- models & engine

export async function renderModels() {
  let data;
  try {
    data = await ctx.api('/api/models');
  } catch (e) {
    return toast(e.message, 'bad');
  }
  $('#disk').textContent = `${fmtBytes(data.disk_free)} free on this drive`;
  renderEngine();
  $('#packs').replaceChildren(...data.packs.map((p) => packCard({ ...p, ...(packState.get(p.id) || {}) }, data.disk_free)));
}

function renderEngine() {
  const st = ctx.S.status || {};
  const e = st.engine || {};
  const cpu = (e.device_type || '').toLowerCase() === 'cpu';
  const label = st.demo ? 'Demo engine' : { ready: cpu ? 'Running on CPU (no GPU)' : 'Ready', starting: 'Starting', error: 'Error',
    missing: 'Not installed', stopped: 'Stopped' }[e.state] || e.state;
  const gb = (b) => (b ? `${(b / 2 ** 30).toFixed(1)} GB` : '-');
  fill($('#engine-card'),
    h('div.state', {}, h(`i.dot.${e.state === 'ready' && !cpu ? 'ok' : e.state === 'starting' ? 'busy' : 'bad'}`),
      h('div', {}, h('b', {}, `Engine: ${label}`), h('div.muted.small', {}, e.error || `ComfyUI ${e.version || ''} on ${e.url || 'localhost'}`))),
    h('dl.kv', {},
      h('dt', {}, 'Device'), h('dd', {}, e.device || '-'),
      h('dt', {}, 'VRAM'), h('dd', {}, e.vram_total ? `${gb(e.vram_free)} free of ${gb(e.vram_total)}` : '-'),
      h('dt', {}, 'Blender'), h('dd', {}, st.blender ? 'found (FBX / OBJ export)' : 'not found'),
      h('dt', {}, 'Queue'), h('dd', {}, st.queue?.current ? 'busy' : 'idle')),
    h('div.actions', {},
      h('button.btn.small', { type: 'button', disabled: st.demo, onclick: restartEngine }, e.state === 'ready' ? 'Restart' : 'Start'),
      h('button.btn.small', { type: 'button', onclick: showLog }, 'Log')));
}

async function restartEngine() {
  try {
    await ctx.api(ctx.S.status?.engine?.state === 'ready' ? '/api/engine/restart' : '/api/engine/start', { method: 'POST' });
    toast('Engine restarting. This takes a minute or two.');
  } catch (e) { toast(e.message, 'bad'); }
}

async function showLog() {
  const text = await ctx.api('/api/engine/log').catch((e) => e.message);
  openModal(h('h3', {}, 'Engine log'), h('pre', {}, text || '(empty)'),
    h('div.modal-actions', {}, h('button.btn', { type: 'button', onclick: () => $('#modal').close() }, 'Close')));
}

function packCard(p, free) {
  const pct = p.downloading ? Math.round(((p.done || 0) / p.size) * 100) : 0;
  const status = p.installed ? h('span.badge.ok', {}, 'Installed')
    : p.downloading ? h('span.badge.accent', {}, `Downloading ${pct}%`)
      : p.error ? h('span.badge.bad', {}, 'Failed') : h('span.badge', {}, p.have ? 'Partial' : 'Not installed');
  const tooBig = !p.installed && p.size - (p.have || 0) > free - 2e9;
  return h('div.card.pack', { dataset: { pack: p.id } },
    h('div.top', {}, h('h3', {}, p.title), h('span', {}, p.required ? h('span.badge.accent', {}, 'Required') : null, ' ', status)),
    h('p', {}, p.blurb),
    h('div.facts', {}, h('span', {}, fmtBytes(p.size)), h('span', {}, p.license), h('span', {}, `${p.files.length} file${p.files.length > 1 ? 's' : ''}`)),
    p.error ? h('p.small', { style: 'color:var(--bad)' }, p.error) : null,
    p.downloading ? h('div.row', {}, h('div.bar', {}, h('i', { style: `width:${pct}%` })),
      h('span.small.muted', {}, `${fmtBytes(p.done)} / ${fmtBytes(p.size)}`),
      h('button.btn.small.ghost', { type: 'button', onclick: () => packAction(p, 'cancel') }, 'Cancel'))
      : h('div.row', {},
        p.installed
          ? h('button.btn.small.danger', { type: 'button', onclick: () => deletePack(p) }, 'Delete')
          : h('button.btn.small.primary', { type: 'button', disabled: tooBig, title: tooBig ? 'Not enough free disk space' : '',
            onclick: () => packAction(p, 'download') }, p.have ? 'Resume download' : `Download ${fmtBytes(p.size - (p.have || 0))}`)));
}

async function packAction(p, action) {
  try {
    await ctx.api(`/api/models/${p.id}/${action}`, { method: 'POST' });
    if (action === 'cancel') packState.delete(p.id);
    renderModels();
  } catch (e) { toast(e.message, 'bad'); }
}

async function deletePack(p) {
  if (!await confirmDialog(`Delete ${p.title}?`, `Frees up to ${fmtBytes(p.size)}. You can download it again later.`)) return;
  try {
    const r = await ctx.api(`/api/models/${p.id}`, { method: 'DELETE' });
    toast(`Removed ${p.title} (${fmtBytes(r.freed)} freed).`, 'ok');
    await refreshStatus();
  } catch (e) { toast(e.message, 'bad'); }
}

async function linkModels() {
  const btn = $('#link-models');
  btn.disabled = true;
  btn.textContent = 'Searching...';
  try {
    const { linked } = await ctx.api('/api/models/link', { method: 'POST' });
    const ok = linked.filter((l) => l.linked);
    const bad = linked.filter((l) => !l.linked);
    if (!linked.length) toast('No matching model files found on this drive.');
    if (ok.length) toast(`Linked ${ok.map((l) => l.file).join(', ')}. No extra disk space used.`, 'ok');
    if (bad.length) toast(`Could not link ${bad.map((l) => l.file).join(', ')}: ${bad[0].error}`, 'bad');
    await refreshStatus();
  } catch (e) { toast(e.message, 'bad'); }
  btn.disabled = false;
  btn.textContent = 'Find models on this PC';
}

async function refreshStatus() {
  const st = await ctx.api('/api/status');
  Object.assign(ctx.S.status, st);
  window.dispatchEvent(new Event('atelier:status'));
  renderModels();
}

export function onModelEvent(ev) {
  packState.set(ev.pack, ev);
  if (!ev.downloading) {
    packState.delete(ev.pack);
    if (ev.error) toast(`Download failed: ${ev.error}`, 'bad');
    else if (!ev.cancelled) { toast('Download finished.', 'ok'); refreshStatus(); }
  }
  if (!$('#view-models').hidden) renderModelsThrottled();
}

let pending = null;
function renderModelsThrottled() {
  if (pending) return;
  pending = setTimeout(() => { pending = null; renderModels(); }, 700);
}
