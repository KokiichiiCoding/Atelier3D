// Atelier3D studio: routing, live state, the Create view (inputs, job card, asset panel, queue).
import { api, subscribe } from './api.js';
import { $, $$, h, fill, toast, prefs, segmented, fmtBytes, fmtCount, fmtDuration, fmtDate, confirmDialog, openModal } from './ui.js';
import { Viewer } from './viewer.js';
import { initPages, renderLibrary, renderModels, onModelEvent } from './pages.js';

const S = {
  status: null,
  jobs: new Map(),          // id -> job
  assets: [],               // newest first
  asset: null,              // the asset in the viewport
  follow: null,             // job id whose progress the viewport shows
  previews: {},             // job id -> {input, structure, painted}
  image: null,              // File for Image mode
  views: {},                // view -> File for Multi-view mode
  conceptJob: prefs.get('conceptJob', null),
};
let viewer;
const ui = {};

const INSPIRE = [
  'A weathered bronze diving helmet with brass rivets and a round glass visor',
  'A chubby red panda knight in leather armour holding a tiny sword',
  'An ornate fantasy treasure chest with gold trim and glowing blue gems',
  'A retro-futuristic ray gun, cream and teal enamel with chrome fins',
  'A cosy ceramic teapot shaped like a sleeping cat, matte glaze',
  'A stylised mossy tree stump with mushrooms and a little door',
  'A vintage film camera, black leather body and silver dials',
  'A cute robot companion with big round eyes and orange panels',
];

// ---------------------------------------------------------------- boot & routing

async function boot() {
  viewer = new Viewer($('#viewport'));
  initCreate();
  initViewportToolbar();
  initKeyboard();
  initPages({ S, api, selectAsset, goCreate: () => { location.hash = '#/create'; } });
  try {
    await resync();
  } catch (e) {
    toast(`Could not reach the studio server: ${e.message}`, 'bad');
  }
  subscribe(onEvent, (up) => $('#engine-pill').classList.toggle('offline', !up));
  window.addEventListener('hashchange', route);
  window.addEventListener('atelier:status', renderStatus);  // pages.js refreshed S.status
  route();
}

async function resync() {
  const [status, jobs, assets] = await Promise.all([api('/api/status'), api('/api/jobs'), api('/api/assets')]);
  S.status = status;
  S.jobs = new Map(jobs.map((j) => [j.id, j]));
  S.assets = assets;
  const running = jobs.find((j) => j.status === 'running');
  if (running && !S.follow) S.follow = running.id;
  renderStatus();
  renderQueue();
  renderRecent();
  renderJobCard();
  renderConcepts();
  if (!S.asset && assets.length && !S.follow) selectAsset(assets[0]);
}

function route() {
  const name = (location.hash.match(/^#\/(\w+)/) || [])[1] || 'create';
  for (const v of $$('.view')) v.hidden = v.dataset.view !== name;
  for (const a of $$('.tabs a')) a.classList.toggle('active', a.dataset.route === name);
  if (name === 'library') renderLibrary();
  if (name === 'models') renderModels();
  if (name === 'create') viewer.resize();
}

// ---------------------------------------------------------------- live events

function onEvent(ev) {
  switch (ev.type) {
    case 'hello':
      if (S.linked) resync();  // a reconnect: catch up on what happened meanwhile
      S.linked = true;
      break;
    case 'engine':
      if (S.status) { S.status.engine = { ...S.status.engine, ...strip(ev) }; renderStatus(); }
      break;
    case 'job': {
      const prev = S.jobs.get(ev.id) || {};
      const job = { ...prev, ...strip(ev) };
      S.jobs.set(ev.id, job);
      if (job.status === 'running' && !S.follow) S.follow = job.id;
      if (job.status === 'failed' && prev.status !== 'failed') toast(`${jobTitle(job)} failed: ${job.error}`, 'bad');
      renderQueue();
      if (job.id === S.follow) renderJobCard();
      if (job.kind === 'concepts' && job.id === S.conceptJob) renderConcepts();
      break;
    }
    case 'preview':
      S.previews[ev.id] = { ...(S.previews[ev.id] || {}), [ev.kind]: ev.url };
      if (ev.id === S.follow) {
        if (ev.kind !== 'input') viewer.load(ev.url, { keepView: true }).then(hideEmpty).catch(() => {});
        renderJobCard();
      }
      break;
    case 'asset': {
      const a = strip(ev);
      const i = S.assets.findIndex((x) => x.id === a.id);
      if (i >= 0) S.assets[i] = { ...S.assets[i], ...a }; else S.assets.unshift(a);
      renderRecent();
      if (a.job_id === S.follow && i < 0) {
        S.follow = nextRunning();
        selectAsset(a, { fresh: true });
        renderJobCard();
      } else if (S.asset?.id === a.id) {
        S.asset = { ...S.asset, ...a };
        renderAssetPanel();
      }
      break;
    }
    case 'asset_deleted':
      S.assets = S.assets.filter((a) => a.id !== ev.id);
      if (S.asset?.id === ev.id) { S.asset = null; viewer.clear(); renderAssetPanel(); showEmpty(); }
      renderRecent();
      break;
    case 'model':
      onModelEvent(ev);
      break;
  }
}

const strip = ({ type, ...rest }) => rest;
const nextRunning = () => [...S.jobs.values()].find((j) => j.status === 'running' && j.kind !== 'concepts')?.id || null;

// ---------------------------------------------------------------- status, banners

function renderStatus() {
  const st = S.status;
  if (!st) return;
  $('#demo-badge').hidden = !st.demo;
  const e = st.engine || {};
  const pill = $('#engine-pill');
  const dot = $('.dot', pill);
  const cpu = (e.device_type || '').toLowerCase() === 'cpu';
  dot.className = 'dot ' + (e.state === 'ready' ? (cpu ? 'bad' : 'ok') : e.state === 'starting' ? 'busy' : e.state === 'error' || e.state === 'missing' ? 'bad' : '');
  $('span', pill).textContent = st.demo ? 'Demo engine' : e.state === 'ready' ? shortGpu(e.device) : `Engine ${e.state || 'stopped'}`;
  pill.title = e.error || e.device || 'Engine status';

  const banners = [];
  const caps = st.capabilities || {};
  if (!st.demo && cpu && e.state === 'ready') {
    banners.push(h('div.banner.bad', {}, h('div', {}, h('b', {}, 'No GPU available'),
      'The engine can only see the CPU, so a model would take many hours. If this PC has an NVIDIA GPU, reboot it (Windows can lose the GPU after a driver crash) or update the driver.')));
  } else if (!st.demo && (e.state === 'error' || e.state === 'missing')) {
    banners.push(h('div.banner.bad', {}, h('div', {}, h('b', {}, 'Engine problem'), e.error || 'The engine is not running.',
      h('div', {}, h('button.btn.small', { type: 'button', onclick: showLog }, 'View log'), ' ',
        h('button.btn.small', { type: 'button', onclick: () => api('/api/engine/restart', { method: 'POST' }).catch((x) => toast(x.message, 'bad')) }, 'Restart')))));
  }
  if (!caps.image3d) {
    banners.push(h('div.banner.info', {}, h('div', {}, h('b', {}, 'One download to go'),
      'Atelier3D needs the Pixal3D core pack (10 GB) before it can build models.',
      h('div', {}, h('a.btn.small.primary', { href: '#/models' }, 'Open Models')))));
  }
  $('#banners').replaceChildren(...banners);

  // capability-dependent controls
  const tBtn = $('#engine button[data-v="trellis2"]');
  tBtn.disabled = !caps.trellis2;
  tBtn.title = caps.trellis2 ? '' : 'Install the TRELLIS.2 pack on the Models tab';
  if (!caps.trellis2 && ui.engine.get() === 'trellis2') ui.engine.set('pixal3d');
  const noMv = $('#no-mv');
  noMv.hidden = !!caps.multiview;
  noMv.replaceChildren(h('div', {}, 'Multi-view needs the Pixal3D multi-view pack (5.6 GB). ', h('a', { href: '#/models' }, 'Models tab')));
  const tm = st.text_models || [];
  const sel = $('#t2i-model');
  const want = prefs.get('t2i', sel.value);
  sel.replaceChildren(...tm.map((m) => h('option', { value: m.id }, m.label)));
  if (tm.some((m) => m.id === want)) sel.value = want;
  sel.disabled = !tm.length;
  const noT2i = $('#no-t2i');
  noT2i.hidden = tm.length > 0;
  noT2i.replaceChildren(h('div', {}, h('b', {}, 'Add a concept model'),
    'Text to 3D first draws concept art. On the Models tab, "Find models on this PC" links ones you already have for free, or download Z-Image Turbo / NetaYume.'));
  renderGenerateButton();
  renderEstimate();
}

const shortGpu = (name) => (name || 'GPU').replace(/^cuda:\d+\s*/, '').replace(/NVIDIA GeForce /, '').replace(/ : .*$/, '');

async function showLog() {
  const text = await api('/api/engine/log').catch((e) => e.message);
  openModal(h('h3', {}, 'Engine log'), h('pre', {}, text || '(empty)'),
    h('div.modal-actions', {}, h('button.btn', { type: 'button', onclick: () => $('#modal').close() }, 'Close')));
}

// ---------------------------------------------------------------- create panel

function initCreate() {
  ui.mode = segmented($('.modes'), (m) => { prefs.set('mode', m); showMode(m); });
  for (const b of $$('.modes button')) b.dataset.v = b.dataset.mode;
  const mode = prefs.get('mode', 'image');
  ui.mode.set(mode);
  showMode(mode);

  ui.quality = segmented($('#quality'), (v) => { prefs.set('quality', v); renderEstimate(); }, prefs.get('quality', 'standard'));
  ui.engine = segmented($('#engine'), (v) => prefs.set('engine', v), prefs.get('engine', 'pixal3d'));
  ui.texture = segmented($('#texture'), (v) => prefs.set('texture', v), prefs.get('texture', '2048'));
  bindPref('#faces', 'faces');
  bindPref('#variations', 'variations');
  bindPref('#concept-count', 'conceptCount');
  bindPref('#t2i-model', 't2i');
  $('#character').checked = prefs.get('character', false);
  $('#character').addEventListener('change', (e) => prefs.set('character', e.target.checked));
  $('#prompt').value = prefs.get('prompt', '');
  $('#prompt').addEventListener('input', (e) => prefs.set('prompt', e.target.value));
  $('#dice').addEventListener('click', () => { $('#seed').value = Math.floor(Math.random() * 2147483647); });
  $('#inspire').addEventListener('click', () => {
    const p = $('#prompt');
    p.value = INSPIRE[(INSPIRE.indexOf(p.value) + 1 + Math.floor(Math.random() * (INSPIRE.length - 1))) % INSPIRE.length];
    prefs.set('prompt', p.value);
    p.focus();
  });

  const styles = ['stylized', 'realistic', 'anime', 'toy', 'lowpoly', 'sculpture'];
  const labels = { stylized: 'Stylised', realistic: 'Realistic', anime: 'Anime', toy: 'Toy', lowpoly: 'Low poly', sculpture: 'Sculpture' };
  let style = prefs.get('style', 'stylized');
  const chips = styles.map((s) => h('button.chip', { type: 'button', role: 'radio', dataset: { v: s },
    onclick: () => { style = s; prefs.set('style', s); paint(); } }, labels[s]));
  const paint = () => chips.forEach((c) => { c.classList.toggle('on', c.dataset.v === style); c.setAttribute('aria-checked', c.dataset.v === style); });
  $('#styles').replaceChildren(...chips);
  paint();
  ui.style = () => style;

  // Image mode: click, drop or paste
  const drop = $('#drop');
  const file = $('#file');
  drop.addEventListener('click', (e) => { if (e.target.id !== 'drop-clear') file.click(); });
  drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.click(); } });
  file.addEventListener('change', () => file.files[0] && setImage(file.files[0]));
  dropTarget(drop, setImage);
  $('#drop-clear').addEventListener('click', (e) => { e.stopPropagation(); setImage(null); });
  document.addEventListener('paste', (e) => {
    const img = [...(e.clipboardData?.files || [])].find((f) => f.type.startsWith('image/'));
    if (!img || location.hash.startsWith('#/library') || location.hash.startsWith('#/models')) return;
    if (ui.mode.get() === 'multiview') return setView('front', img);
    ui.mode.set('image'); showMode('image'); setImage(img);
  });

  // Multi-view slots
  for (const slot of $$('.view-slot')) {
    const v = slot.dataset.viewSlot;
    const input = h('input', { type: 'file', accept: 'image/*', hidden: true, onchange: (e) => e.target.files[0] && setView(v, e.target.files[0]) });
    slot.append(input);
    slot.tabIndex = 0;
    slot.setAttribute('role', 'button');
    slot.setAttribute('aria-label', `Choose the ${v} view`);
    slot.addEventListener('click', () => input.click());
    slot.addEventListener('keydown', (e) => { if (e.key === 'Enter') input.click(); });
    dropTarget(slot, (f) => setView(v, f));
  }

  $('#generate').addEventListener('click', generate);
}

function bindPref(sel, key) {
  const el = $(sel);
  const v = prefs.get(key, null);
  if (v !== null && [...el.options].some((o) => o.value === String(v))) el.value = v;
  el.addEventListener('change', () => { prefs.set(key, el.value); renderEstimate(); });
}

function dropTarget(el, onFile) {
  el.addEventListener('dragover', (e) => { e.preventDefault(); el.classList.add('over'); });
  el.addEventListener('dragleave', () => el.classList.remove('over'));
  el.addEventListener('drop', (e) => {
    e.preventDefault();
    el.classList.remove('over');
    const f = [...e.dataTransfer.files].find((x) => x.type.startsWith('image/'));
    if (f) onFile(f); else toast('Drop an image file (PNG, JPG or WebP).', 'bad');
  });
}

function showMode(m) {
  for (const p of $$('.mode-pane')) p.hidden = p.dataset.pane !== m;
  $('#engine-field').hidden = m === 'multiview';
  renderGenerateButton();
}

function setImage(f) {
  S.image = f;
  const img = $('#drop-img');
  if (img.src) URL.revokeObjectURL(img.src);
  img.hidden = !f;
  $('.drop-empty').hidden = !!f;
  $('#drop-clear').hidden = !f;
  if (f) img.src = URL.createObjectURL(f); else img.removeAttribute('src');
  renderGenerateButton();
}

function setView(v, f) {
  S.views[v] = f;
  const slot = $(`.view-slot[data-view-slot="${v}"]`);
  $('img', slot)?.remove();
  slot.classList.add('filled');
  slot.prepend(h('img', { src: URL.createObjectURL(f), alt: `${v} view` }));
  renderGenerateButton();
}

function renderGenerateButton() {
  const m = ui.mode?.get();
  if (!m || !S.status) return;
  const caps = S.status.capabilities || {};
  const btn = $('#generate');
  $('#generate-label').textContent = m === 'text' ? 'Draw concepts' : 'Generate 3D model';
  btn.disabled = m === 'text' ? !caps.text3d : m === 'image' ? !caps.image3d : !caps.multiview;
}

function settings3d() {
  const seed = $('#seed').value.trim();
  return {
    quality: ui.quality.get(), faces: Number($('#faces').value), texture: Number(ui.texture.get()),
    engine: ui.engine.get(), variations: Number($('#variations').value), name: $('#asset-name').value.trim(),
    ...(seed ? { seed: Number(seed) } : {}),
  };
}

async function generate() {
  const m = ui.mode.get();
  const btn = $('#generate');
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    if (m === 'text') return await drawConcepts();
    const s = settings3d();
    const form = new FormData();
    for (const [k, v] of Object.entries(s)) if (!(m === 'multiview' && k === 'engine')) form.append(k, v);
    let res;
    if (m === 'image') {
      if (!S.image) return toast('Choose an image first: drop, paste or click the box.', 'bad');
      form.append('image', S.image);
      res = await api('/api/generate/image', { method: 'POST', form });
    } else {
      if (!S.views.front) return toast('The front view is required.', 'bad');
      for (const [v, f] of Object.entries(S.views)) form.append(v, f);
      res = await api('/api/generate/multiview', { method: 'POST', form });
    }
    onSubmitted(res.jobs);
  } catch (e) {
    toast(e.message, 'bad');
  } finally {
    renderGenerateButton();
  }
}

function onSubmitted(jobs) {
  for (const j of jobs) S.jobs.set(j.id, j);
  S.follow = jobs[0].id;
  renderQueue();
  renderJobCard();
  toast(jobs.length > 1 ? `${jobs.length} variations queued.` : 'Queued. You can keep working while it builds.', 'ok');
}

// ---------------------------------------------------------------- text to 3D: concepts

async function drawConcepts() {
  const prompt = $('#prompt').value.trim();
  if (!prompt) { $('#prompt').focus(); return toast('Describe what to make first.', 'bad'); }
  const body = { prompt, style: ui.style(), character: $('#character').checked, model: $('#t2i-model').value,
    count: Number($('#concept-count').value) };
  const { job } = await api('/api/generate/concepts', { method: 'POST', json: body });
  S.jobs.set(job.id, job);
  S.conceptJob = job.id;
  prefs.set('conceptJob', job.id);
  renderConcepts();
  renderQueue();
}

function renderConcepts() {
  const job = S.jobs.get(S.conceptJob);
  const box = $('#concepts');
  box.hidden = !job;
  if (!job) return;
  const grid = $('#concept-grid');
  const status = $('#concepts-status');
  if (job.status === 'done' && job.result?.images) {
    status.textContent = '';
    grid.replaceChildren(...job.result.images.map((url, i) => h('div.concept', { tabindex: 0, title: 'Build this one in 3D' },
      h('img', { src: url, alt: `Concept ${i + 1}`, onclick: () => openModal(h('img.big', { src: url, alt: '' })) }),
      h('button.btn.primary.make', { type: 'button', onclick: (e) => { e.stopPropagation(); fromConcept(job.id, i); } }, 'Make 3D'))));
  } else if (job.status === 'failed' || job.status === 'cancelled') {
    status.textContent = job.status === 'failed' ? 'Failed' : 'Cancelled';
    grid.replaceChildren(h('p.muted.small', {}, job.error || 'No concepts were drawn.'));
  } else {
    const p = job.progress;
    status.textContent = job.status === 'queued' ? 'Waiting in the queue' : p?.eta ? `${p.label} - ${fmtDuration(p.eta)} left` : p?.label || 'Starting';
    const n = job.params?.count || 4;
    if (grid.children.length !== n || !grid.firstChild.classList.contains('skeleton')) {
      grid.replaceChildren(...Array.from({ length: n }, () => h('div.concept.skeleton')));
    }
  }
}

async function fromConcept(jobId, index) {
  try {
    const res = await api('/api/generate/from-concept', { method: 'POST', json: { job_id: jobId, index, ...settings3d() } });
    onSubmitted(res.jobs);
  } catch (e) { toast(e.message, 'bad'); }
}

// ---------------------------------------------------------------- estimate

function renderEstimate() {
  const q = ui.quality?.get();
  if (!q) return;
  const runs = S.assets.filter((a) => a.params?.quality === q && a.seconds).map((a) => a.seconds).sort((a, b) => a - b);
  const el = $('#estimate');
  if (S.status?.demo) el.textContent = 'Demo mode: models are procedural stand-ins.';
  else if (runs.length) el.textContent = `About ${fmtDuration(runs[Math.floor(runs.length / 2)])} on this PC (median of ${runs.length} ${q} run${runs.length > 1 ? 's' : ''}).`;
  else el.textContent = 'The first run also loads the models, so give it a few extra minutes.';
}

// ---------------------------------------------------------------- job card (viewport)

function jobTitle(j) {
  const p = j.params || {};
  if (j.kind === 'concepts') return `Concepts: ${p.prompt?.slice(0, 40) || ''}`;
  return p.name || p.source?.prompt?.split(/\s+/).slice(0, 6).join(' ') || p.source?.filename || 'New model';
}

function renderJobCard() {
  const card = $('#job-card');
  const job = S.jobs.get(S.follow);
  if (!job || job.kind === 'concepts' || job.status === 'done' || job.status === 'cancelled') { card.hidden = true; return; }
  card.hidden = false;
  const p = job.progress || {};
  const pv = S.previews[job.id] || {};
  const failed = job.status === 'failed';
  const sub = failed ? 'Failed' : job.status === 'queued' ? queuePosition(job) : p.eta ? `${p.label} - about ${fmtDuration(p.eta)} left` : p.label || 'Starting';
  hideEmpty();
  fill(card,
    h('div.head', {},
      pv.input ? h('img', { src: pv.input, alt: '' }) : null,
      h('div.t', {}, h('b', {}, jobTitle(job)), h('span.muted.small', {}, sub))),
    failed ? h('p.small', { style: 'color:var(--bad);margin:0' }, job.error)
      : h(`div.bar${job.status === 'queued' || !p.overall ? '.indeterminate' : ''}`, {}, h('i', { style: `width:${Math.round((p.overall || 0) * 100)}%` })),
    p.stages?.length ? h('ul.stages', {}, p.stages.map((s) => h(`li.${s.state}`, {}, s.label))) : null,
    h('div.foot', {},
      h('span.muted.small', {}, p.elapsed ? `${fmtDuration(p.elapsed)} elapsed` : ''),
      failed ? h('button.btn.small', { type: 'button', onclick: () => { S.follow = nextRunning(); renderJobCard(); } }, 'Dismiss')
        : h('button.btn.small.ghost', { type: 'button', onclick: () => cancelJob(job.id) }, 'Cancel')));
}

function queuePosition(job) {
  const ahead = [...S.jobs.values()].filter((j) => (j.status === 'queued' || j.status === 'running') && j.created < job.created).length;
  return ahead ? `Waiting: ${ahead} ahead in the queue` : 'Starting';
}

async function cancelJob(id) {
  try { await api(`/api/jobs/${id}/cancel`, { method: 'POST' }); } catch (e) { toast(e.message, 'bad'); }
}

// ---------------------------------------------------------------- queue & recent (right panel)

function renderQueue() {
  const live = [...S.jobs.values()].filter((j) => j.status === 'queued' || j.status === 'running');
  const failed = [...S.jobs.values()].filter((j) => j.status === 'failed' && !j.dismissed).slice(0, 3);
  const items = [...live.sort((a, b) => a.created - b.created), ...failed];
  $('#queue-empty').hidden = items.length > 0;
  $('#queue').replaceChildren(...items.map((j) => {
    const p = j.progress || {};
    return h('li.clickable', { onclick: () => { if (j.kind !== 'concepts') { S.follow = j.id; renderJobCard(); } } },
      h('div.top', {}, h('b', {}, jobTitle(j)),
        h(`span.badge${j.status === 'failed' ? '.bad' : j.status === 'running' ? '.accent' : ''}`, {}, j.status === 'running' ? `${Math.round((p.overall || 0) * 100)}%` : j.status),
        j.status === 'failed'
          ? h('button.icon-btn', { type: 'button', 'aria-label': 'Dismiss', onclick: async (e) => { e.stopPropagation(); j.dismissed = true; await api(`/api/jobs/${j.id}`, { method: 'DELETE' }).catch(() => {}); S.jobs.delete(j.id); renderQueue(); } }, '×')
          : h('button.icon-btn', { type: 'button', 'aria-label': 'Cancel', title: 'Cancel', onclick: (e) => { e.stopPropagation(); cancelJob(j.id); } }, '×')),
      j.status === 'failed' ? h('div.err', {}, j.error)
        : h(`div.bar${j.status === 'queued' ? '.indeterminate' : ''}`, {}, h('i', { style: `width:${Math.round((p.overall || 0) * 100)}%` })));
  }));
  const pill = $('#queue-pill');
  pill.hidden = !live.length;
  pill.textContent = `${live.length} in queue`;
  pill.onclick = () => { location.hash = '#/create'; };
}

function renderRecent() {
  $('#recent').replaceChildren(...S.assets.slice(0, 12).map((a) => h(`button.thumb-btn${S.asset?.id === a.id ? '.current' : ''}`,
    { type: 'button', title: a.name, onclick: () => selectAsset(a) }, h('img', { src: a.thumb, alt: a.name, loading: 'lazy' }))));
  renderEstimate();
}

// ---------------------------------------------------------------- asset panel

async function selectAsset(a, { fresh = false } = {}) {
  S.asset = a;
  if (!fresh && S.follow && S.jobs.get(S.follow)?.status !== 'running') S.follow = null;
  renderAssetPanel();
  renderRecent();
  renderJobCard();
  hideEmpty();
  try {
    await viewer.load(a.url, { keepView: fresh });
    if (fresh || !a.thumb_rendered) setTimeout(() => saveThumb(a), 120);
  } catch (e) {
    toast(`Could not load ${a.name}: ${e.message}`, 'bad');
  }
  renderStats();
}

async function saveThumb(a) {
  const blob = await viewer.snapshot({ square: true, size: 512 });
  if (!blob) return;
  try {
    await api(`/api/assets/${a.id}/thumb`, { method: 'PUT', body: blob, headers: { 'Content-Type': 'image/png' } });
    a.thumb = `/files/${a.id}/thumb.png?v=${Date.now()}`;
    a.thumb_rendered = true;
    renderRecent();
  } catch { /* the input picture stays as the thumbnail */ }
}

function renderStats() {
  const a = S.asset;
  const el = $('#vp-stats');
  el.hidden = !a;
  if (!a) return;
  const s = a.stats || {};
  el.replaceChildren(
    h('span', {}, h('b', {}, fmtCount(s.faces)), ' tris'),
    h('span', {}, h('b', {}, fmtCount(s.vertices)), ' verts'),
    h('span', {}, h('b', {}, String(s.textures ?? 0)), ' textures'),
    h('span', {}, h('b', {}, fmtBytes(s.bytes)), ' GLB'));
}

function renderAssetPanel() {
  const a = S.asset;
  const panel = $('#asset-panel');
  panel.hidden = !a;
  if (!a) return;
  const p = a.params || {};
  const src = a.source || p.source || {};
  const nameInput = h('input', { value: a.name, maxlength: 80, 'aria-label': 'Model name',
    onchange: async (e) => {
      try { Object.assign(a, await api(`/api/assets/${a.id}`, { method: 'PATCH', json: { name: e.target.value } })); renderRecent(); }
      catch (err) { toast(err.message, 'bad'); e.target.value = a.name; }
    } });
  const star = h(`button.icon-btn.star${a.favorite ? '.on' : ''}`, { type: 'button', 'aria-label': 'Favourite', 'aria-pressed': a.favorite,
    onclick: async () => {
      Object.assign(a, await api(`/api/assets/${a.id}`, { method: 'PATCH', json: { favorite: !a.favorite } }));
      renderAssetPanel();
    } }, a.favorite ? '★' : '☆');
  const blender = S.status?.blender;
  const dl = (fmt, label, title) => h('a.btn', { href: `/api/assets/${a.id}/download/${fmt}`, download: '', title }, label);
  const client = (fmt, label) => h('button.btn', { type: 'button', onclick: () => exportClient(a, fmt) }, label);
  const maps = Object.entries(a.maps || {});
  fill(panel,
    h('div.asset-title', {}, nameInput, star),
    h('span.muted.small', {}, `${fmtDate(a.created)} · ${({ image3d: 'from an image', multiview3d: 'from views' })[a.kind] || ''}${src.mode === 'text' ? ' (text)' : ''}`),
    src.prompt ? h('div.source', {}, src.prompt) : null,
    h('div.downloads', {},
      h('a.btn.primary.main', { href: `/api/assets/${a.id}/download/glb`, download: '' }, 'Download GLB'),
      blender ? dl('fbx', 'FBX', 'Converted by Blender') : h('button.btn', { type: 'button', disabled: true, title: 'Install Blender for FBX' }, 'FBX'),
      blender ? dl('obj', 'OBJ', 'OBJ + MTL + textures (Blender)') : client('obj', 'OBJ'),
      client('usdz', 'USDZ'), client('stl', 'STL'), client('ply', 'PLY'),
      maps.length ? dl('textures', 'Textures', 'All PBR maps as PNG') : null),
    maps.length ? h('div', {}, h('h2', {}, 'PBR maps'), h('div.maps', { style: 'margin-top:8px' },
      maps.map(([k, url]) => h('button', { type: 'button', title: k, onclick: () => openModal(h('h3', {}, mapLabel(k)), h('img.big', { src: url, alt: k })) },
        h('img', { src: url, alt: '', loading: 'lazy' }), h('span', {}, mapLabel(k)))))) : null,
    h('dl.kv', {},
      h('dt', {}, 'Quality'), h('dd', {}, p.quality || '-'),
      h('dt', {}, 'Engine'), h('dd', {}, a.kind === 'multiview3d' ? 'Pixal3D multi-view' : p.engine === 'trellis2' ? 'TRELLIS.2' : 'Pixal3D'),
      h('dt', {}, 'Polycount'), h('dd', {}, `${fmtCount(p.faces)} target`),
      h('dt', {}, 'Texture'), h('dd', {}, p.texture ? `${p.texture / 1024}K` : '-'),
      h('dt', {}, 'Seed'), h('dd', {}, String(p.seed ?? '-')),
      h('dt', {}, 'Build time'), h('dd', {}, fmtDuration(a.seconds) || '-')),
    h('div.actions', {},
      h('button.btn.small', { type: 'button', title: 'Same input, new seed', onclick: () => remix(a, {}) }, 'Remix'),
      p.quality !== 'high' ? h('button.btn.small', { type: 'button', onclick: () => remix(a, { quality: 'high', seed: p.seed }) }, 'Redo in High') : null,
      h('button.btn.small', { type: 'button', onclick: async () => {
        const blob = await viewer.snapshot();
        save(blob, `${slug(a.name)}.png`);
      } }, 'Screenshot'),
      h('button.btn.small', { type: 'button', title: 'A 6-second spin of the viewport, for sharing', onclick: async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        b.textContent = 'Recording...';
        try { save(await viewer.recordTurntable(6), `${slug(a.name)}_turntable.webm`); }
        catch (err) { toast(err.message, 'bad'); }
        b.disabled = false;
        b.textContent = 'Turntable video';
      } }, 'Turntable video'),
      h('button.btn.small.danger', { type: 'button', onclick: () => deleteAsset(a) }, 'Delete')));
}

const mapLabel = (k) => ({ base_color: 'Color', metallic: 'Metal', roughness: 'Rough', normal: 'Normal', ao: 'AO' })[k] || k;
const slug = (s) => (s || 'model').replace(/[^\w\- ]+/g, '').trim().replace(/\s+/g, '_') || 'model';

function save(blob, name) {
  const a = h('a', { href: URL.createObjectURL(blob), download: name });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

async function exportClient(a, fmt) {
  try {
    if (S.asset?.id !== a.id) await selectAsset(a);
    save(await viewer.exportAs(fmt), `${slug(a.name)}.${fmt}`);
  } catch (e) { toast(`Export failed: ${e.message}`, 'bad'); }
}

async function remix(a, overrides) {
  try {
    const res = await api(`/api/assets/${a.id}/remix`, { method: 'POST', json: overrides });
    onSubmitted(res.jobs);
  } catch (e) { toast(e.message, 'bad'); }
}

async function deleteAsset(a) {
  if (!await confirmDialog(`Delete "${a.name}"?`, 'The model, its textures and previews are removed from disk. This cannot be undone.')) return;
  try { await api(`/api/assets/${a.id}`, { method: 'DELETE' }); } catch (e) { toast(e.message, 'bad'); }
}

// ---------------------------------------------------------------- viewport toolbar & keys

function initViewportToolbar() {
  ui.view = segmented($('#view-mode'), (m) => { viewer.setMode(m); prefs.set('view', m); }, prefs.get('view', 'lit'));
  viewer.setMode(ui.view.get());
  const env = $('#env');
  env.value = prefs.get('env', 'studio');
  viewer.setEnv(env.value);
  env.addEventListener('change', () => { viewer.setEnv(env.value); prefs.set('env', env.value); });
  const toggle = (id, fn, initial) => {
    const b = $(id);
    const set = (on) => { b.setAttribute('aria-pressed', on); fn(on); };
    set(initial);
    b.addEventListener('click', () => set(b.getAttribute('aria-pressed') !== 'true'));
    return () => b.click();
  };
  ui.turntable = toggle('#turntable', (on) => viewer.setTurntable(on), false);
  ui.grid = toggle('#grid', (on) => viewer.setGrid(on), true);
  $('#frame').addEventListener('click', () => viewer.frame());
  $('#shot').addEventListener('click', async () => save(await viewer.snapshot(), `${slug(S.asset?.name)}.png`));
  $('#fullscreen').addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen(); else $('#stage').requestFullscreen?.();
  });
}

function initKeyboard() {
  document.addEventListener('keydown', (e) => {
    if (!$('#view-create').hidden && (e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); generate(); return; }
    if (e.target.closest('input, textarea, select, [contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return;
    if ($('#view-create').hidden || $('#modal').open) return;
    const modes = ['lit', 'base', 'clay', 'wire', 'normals', 'uv'];
    if (e.key >= '1' && e.key <= '6') { ui.view.set(modes[e.key - 1], true); return; }
    if (e.key === 't') ui.turntable();
    else if (e.key === 'g') ui.grid();
    else if (e.key === 'f') viewer.frame();
  });
}

function hideEmpty() { $('#vp-empty').hidden = true; }
function showEmpty() { $('#vp-empty').hidden = false; $('#vp-stats').hidden = true; }

boot();
