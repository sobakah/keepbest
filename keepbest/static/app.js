// keepbest's page: what the flow in app.py and core_logic.py shows, and its
// open question. Everything comes from /api/state (keepbest/web.py); answers
// go back to /api/answer as the keys a user types in the terminal, so the
// flow is the terminal's, step for step. File names are only ever text.
import { html, render, useState, useEffect, useRef } from '/static/vendor/preact-htm.js';

async function api(path, body) {
  const options = {};
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch('/api' + path, options);
  } catch (err) {
    throw new Error('keepbest is not responding. Is it still running?');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

const STEPS = [['settings', 'Settings'], ['1', '1 Identical'], ['2', '2 Remux'], ['3', '3 Visual'], ['summary', 'Summary']];
const MODES = { trash: ['TRASH', 'b-keep'], dry_run: ['DRY RUN', 'b-plain'], permanent: ['PERMANENT', 'b-danger'] };
const STATUS = { done: ['DONE', 'b-ok'], skipped: ['SKIPPED', 'b-borderline'], ignored: ['NOT DUP', 'b-notdup'],
  pending: ['OPEN', 'b-plain'] };
const MARKS = { success: '✓', warn: '!', error: '✗' };
const URL_PATTERN = /https?:\/\/[^\s)\]]*[^\s)\].,;:!?'"]/g;

// The step a banner starts: "Stage 2 · Remuxed videos" is step 2.
function stepOf(text) {
  const stage = /^Stage (\d)/.exec(text);
  if (stage) return stage[1];
  return /^Summary/.test(text) ? 'summary' : null;
}

// Text with its web links clickable (another keepbest's page); never markup from the text.
function Linked({ text }) {
  const parts = [];
  let last = 0;
  const value = String(text ?? '');
  for (const match of value.matchAll(URL_PATTERN)) {
    if (match.index > last) parts.push(value.slice(last, match.index));
    parts.push(html`<a href=${match[0]} target="_blank" rel="noopener noreferrer">${match[0]}</a>`);
    last = match.index + match[0].length;
  }
  parts.push(value.slice(last));
  return parts;
}

const Mode = ({ mode }) => {
  const [text, cls] = MODES[mode] || [String(mode).toUpperCase(), 'b-plain'];
  return html`<span class=${'badge ' + cls}>${text}</span>`;
};
const Verdict = ({ borderline }) => (borderline
  ? html`<span class="badge b-borderline">BORDERLINE</span>` : html`<span class="badge b-ok">CONFIDENT</span>`);
const Key = ({ k }) => (k === '' ? html`<kbd>Enter</kbd>` : html`<kbd>${k}</kbd>`);

// --- the log ----------------------------------------------------------------------
function VisualTable({ group, keepIndex = 0, done = false, actions = null }) {
  return html`<div class="table-box"><table>
    <thead><tr><th>#</th><th>Action</th><th>File</th><th>Format</th><th>Type</th><th class="num">Resolution</th>
      <th class="num">Size</th><th class="num">BPP</th><th class="num">Diff</th>${actions && html`<th></th>`}</tr></thead>
    <tbody>${group.images.map((image, i) => {
      const kept = i === keepIndex;
      const badge = done ? (kept ? ['KEPT', 'b-keep'] : ['GONE', 'b-gone']) : (kept ? ['KEEP', 'b-keep'] : ['REMOVE', 'b-remove']);
      const hot = image.diff !== null && image.diff > group.borderlineAbove;
      return html`<tr class=${kept ? 'kept' : ''} key=${i}>
        <td class="mono">${i}</td><td><span class=${'badge ' + badge[1]}>${badge[0]}</span></td>
        <td class="file">${image.name}</td><td>${image.format}</td><td>${image.type}</td>
        <td class="num">${image.width}×${image.height}</td><td class="num">${image.size}</td>
        <td class="num">${image.bpp ?? ''}</td>
        <td class=${'num' + (hot ? ' diff-hot' : '')}>${image.diff === null ? '—' : image.diff}</td>
        ${actions && html`<td>${actions(i)}</td>`}
      </tr>`;
    })}</tbody>
  </table></div>`;
}

function ExactTable({ group, keepIndex = 0, actions = null }) {
  const remux = group.kind === 'remux';
  return html`<div class="table-box"><table>
    <thead><tr><th>#</th><th>Action</th><th>File</th>${remux && html`<th class="num">Size</th><th>Streams</th>`}
      ${actions && html`<th></th>`}</tr></thead>
    <tbody>${group.files.map((file, i) => {
      const kept = keepIndex === null || i === keepIndex;
      return html`<tr class=${i === keepIndex ? 'kept' : ''} key=${i}>
        <td class="mono">${i}</td><td><span class=${'badge ' + (kept ? 'b-keep' : 'b-remove')}>${kept ? 'KEEP' : 'REMOVE'}</span></td>
        <td class="file">${file.name}</td>
        ${remux && html`<td class="num">${file.size}</td><td class="small muted">${file.streams}</td>`}
        ${actions && html`<td>${actions(i)}</td>`}
      </tr>`;
    })}</tbody>
  </table></div>`;
}

function Summary({ event }) {
  const s = event.stats;
  const dry = event.mode === 'dry_run';
  const verb = dry ? 'to remove' : 'removed';
  const tiles = [['Scanned', s.scanned], [`Exact copies ${verb}`, s.exact_deleted]];
  if (s.remux_deleted) tiles.push([`Remuxed videos ${verb}`, s.remux_deleted]);
  tiles.push([`Identical images ${verb}`, s.similar_deleted], [dry ? 'To rename' : 'Renamed', s.renamed],
    ['Groups skipped', s.groups_skipped]);
  if (s.groups_ignored) tiles.push(['Not duplicates', s.groups_ignored]);
  tiles.push(['Failed', s.failed], [dry ? 'Space that would be freed' : 'Space freed', event.sizeFreed]);
  return html`<div class="stack-tight">
    <dl class="tiles">${tiles.map(([label, value]) => html`<div class="tile" key=${label}><dt>${label}</dt><dd>${value}</dd></div>`)}</dl>
    ${event.totalsNote && html`<p class="line k-dim">${event.totalsNote}</p>`}
    ${dry && html`<p class="line k-dim">Nothing was deleted or renamed.</p>`}
    ${event.logEntries > 0 && html`<p class="line k-dim">Log (${event.logEntries} entries): <span class="mono">${event.logPath}</span></p>`}
    ${event.note && html`<p class="line">${event.note}</p>`}
  </div>`;
}

function Item({ event }) {
  switch (event.kind) {
    case 'info':
      return html`<p class="line"><${Linked} text=${event.text} /></p>`;
    case 'success': case 'warn': case 'error':
      return html`<p class=${'line k-' + event.kind}><span class="mark">${MARKS[event.kind]}</span><${Linked} text=${event.text} /></p>`;
    case 'dim':
      return html`<p class="line k-dim">${event.text}</p>`;
    case 'outcome':
      return html`<p class=${'outcome tone-' + event.tone}><span>${event.symbol}</span><span>${event.label}</span>
        <span class="name">${event.name}</span></p>`;
    case 'exact_heading':
      return html`<h3 class="group-head"><span class="mono">[${event.number}/${event.total}]</span>
        <span class=${'badge ' + (event.groupKind === 'remux' ? 'b-keep' : 'b-notdup')}>${event.groupKind === 'remux' ? 'REMUX' : 'IDENTICAL'}</span>
        ${event.tracks === 'reordered' && html`<span class="badge b-borderline">TRACK ORDER</span>`}
        ${event.tracks && event.tracks !== 'reordered' && event.tracks !== 'same' && html`<span class="badge b-borderline">TRACKS DIFFER</span>`}
        <span class="muted">${event.count} files · ${event.groupKind === 'remux' ? 'same video stream in different containers' : `${event.size} each`}</span></h3>`;
    case 'exact_table':
      return html`<${ExactTable} group=${event.group} keepIndex=${event.keepIndex} />`;
    case 'group_line':
      return html`<h3 class="group-head"><span class="mono">[${event.position}]</span><${Verdict} borderline=${event.borderline} />
        <span class="muted">worst difference ${event.worst} of ${event.limit} allowed (0 = pixel-identical)</span></h3>`;
    case 'group_table':
      return html`<${VisualTable} group=${event.group} keepIndex=${event.keepIndex} done=${event.done} />`;
    case 'stage3_header':
      return html`<p class="line row"><span>Limit ${event.limit} (${event.preset})</span><span class="muted">·</span>
        <span>Confirm: ${event.confirm}</span><${Mode} mode=${event.mode} /></p>`;
    case 'summary':
      return html`<${Summary} event=${event} />`;
    case 'folder_header':
      return html`<p class="line">Main folder: ${event.main_on ? 'included' : 'excluded'} · ${event.files_text}</p>`;
    case 'progress':
      if (!event.total) return null;
      return event.running
        ? html`<div class="progress"><span class="spinner" aria-hidden="true"></span><span>${event.label}…</span>
            <span class="bar"><span style=${{ width: `${event.total ? (100 * event.done) / event.total : 100}%` }}></span></span>
            <span class="mono small">${event.done}/${event.total}</span></div>`
        : html`<p class="line k-dim">${event.label}: ${event.total} done</p>`;
    case 'status':
      return event.running ? html`<div class="progress"><span class="spinner" aria-hidden="true"></span>${event.text}</div>` : null;
    case 'answer':
      return html`<p class="answer">${event.text}</p>`;
    default:
      return null;
  }
}

// --- answering --------------------------------------------------------------------
// The key buttons of a screen's menu; keys handled elsewhere on the screen are left out.
function Menu({ question, answer, sending, skip = () => false }) {
  const expand = (key) => {
    const m = /^([a-z]*)(\d+)-\1(\d+)$/.exec(key);
    if (!m) return [key];
    const keys = [];
    for (let n = Number(m[2]); n <= Number(m[3]); n++) keys.push(m[1] + n);
    return keys;
  };
  return html`<div class="row" style=${{ alignItems: 'flex-start', gap: '14px 28px' }}>
    ${question.menu.map(([title, items]) => {
      const buttons = items.filter(([key]) => !skip(key)).flatMap(([key, label]) => expand(key).map((k) => [k, label]));
      if (!buttons.length) return null;
      return html`<div class="menu-group" key=${title}><span class="title">${title}</span><div class="row">
        ${buttons.map(([k, label]) => html`<button class="btn" key=${k} disabled=${sending}
          onClick=${() => answer(k)}>${label}${k !== label ? html` <${Key} k=${k} />` : ''}</button>`)}
      </div></div>`;
    })}
  </div>`;
}

function Primary({ question, answer, sending, danger = false }) {
  if (!question.primary) return null;
  return html`<div><button class=${'btn ' + (danger ? 'btn-danger' : 'btn-primary')} disabled=${sending}
    onClick=${() => answer('')}>${question.primary} <kbd>Enter</kbd></button></div>`;
}

function SettingRow({ row, set, sending }) {
  if (row.type === 'note') return html`<p class=${'note' + (row.style === 'hint' ? ' hint' : '')}>${row.text}</p>`;
  if (row.type === 'folders') {
    const subs = row.chosen === row.total ? `all ${row.total} subfolders` : `${row.chosen} of ${row.total} subfolders`;
    return html`<div class="setting"><div class="label"><kbd>${row.key}</kbd>${row.label}</div>
      <div class="value"><span>${row.mainOn ? 'main folder' : html`<span class="k-warn">main folder excluded</span>`} + ${subs}</span>
        <button class="btn btn-small" disabled=${sending} onClick=${() => set(row.key)}>Choose folders <kbd>${row.key}</kbd></button></div></div>`;
  }
  const locked = row.locked !== null && row.locked !== undefined;
  return html`<div class=${'setting' + (locked ? ' locked' : '')}>
    <div class="label"><kbd>${row.key}</kbd>${row.label}</div>
    <div class="value">
      <div class="choices" role="group" aria-label=${row.label}>
        ${row.options.filter((o) => !locked || o.current).map((o, i) => html`<button key=${o.label}
          class=${'choice' + (o.current ? ' on' : '') + (o.danger ? ' danger' : '')} aria-pressed=${o.current}
          disabled=${sending || locked || o.unavailable || o.current}
          onClick=${() => set(row.key, row.options.indexOf(o))}>${o.label}</button>`)}
      </div>
      ${locked && html`<span class="small muted">Locked: ${row.locked}</span>`}
    </div>
  </div>`;
}

function Panel({ panel }) {
  const kind = panel.dry ? 'Dry run' : 'Session';
  return html`<div class="panel">
    <b>Saved ${kind.toLowerCase()} from ${panel.when}</b> ·
    ${panel.finished
      ? ` finished: ${panel.groups} group(s), ${panel.to_remove} file(s) to remove, ${panel.to_rename} to rename`
      : ` ${panel.groups} groups: ${panel.done} done, ${panel.skipped} skipped${panel.ignored ? `, ${panel.ignored} not duplicates` : ''}, ${panel.open - panel.skipped} open`}
    ${panel.description && html`<div class="small">Matched with: ${panel.description}.</div>`}
  </div>`;
}

function StartForm({ question, answer, sending }) {
  const q = question;
  const set = (key, index) => answer(index === undefined ? key : { set: [key, index] });
  return html`<div class="stack">
    <dl class="facts">
      <dt>Folder</dt><dd class="row"><span class="mono" title=${q.target}>${q.targetShort}</span>
        <button class="btn btn-small" disabled=${sending} onClick=${() => answer('c')}>Change directory <kbd>c</kbd></button></dd>
      <dt>Files</dt><dd><b>${q.inScope}</b> in scope <span class="muted">(${q.inScope - q.scopeVideos} images, ${q.scopeVideos} videos)</span>
        ${q.moreInSubfolders > 0 && html` <span class="muted">· ${q.moreInSubfolders} more in subfolders</span>`}</dd>
    </dl>
    ${q.panel && html`<${Panel} panel=${q.panel} />`}
    <div class="settings">
      <div class="row" style=${{ paddingBottom: '6px' }}><h2>Settings</h2><${Mode} mode=${q.mode} /></div>
      ${q.rows.map((row, i) => html`<${SettingRow} key=${i} row=${row} set=${set} sending=${sending} />`)}
    </div>
    ${q.blocked && html`<p class="alert">${q.blocked}</p>`}
    ${!q.blocked && !q.primary && q.nothingToScan && html`<p class="alert">Fewer than two files in scope - nothing to compare.</p>`}
    <div class="row">
      <${Primary} question=${q} answer=${answer} sending=${sending} />
      ${q.keys.includes('e') && html`<button class="btn" disabled=${sending} onClick=${() => answer('e')}>Review its groups again for real <kbd>e</kbd></button>`}
      ${q.keys.includes('n') && html`<button class="btn" disabled=${sending} onClick=${() => answer('n')}>Start over: discard the session <kbd>n</kbd></button>`}
    </div>
  </div>`;
}

function FolderTree({ question, answer, sending }) {
  const q = question;
  return html`<div class="stack">
    <p class="muted">A folder you switch off loses only its own files; you are asked whether the folders below it change too.</p>
    <label class="switch"><input type="checkbox" checked=${q.header.main_on} disabled=${sending} onChange=${() => answer('m')} />
      <span style=${{ flex: '1 1 auto' }}><b>Main folder</b> <span class="muted">· ${q.header.files_text}</span></span><kbd>m</kbd></label>
    <div class="row"><span class="small muted">Subfolders</span>
      <button class="btn btn-small" disabled=${sending} onClick=${() => answer('a')}>All <kbd>a</kbd></button>
      <button class="btn btn-small" disabled=${sending} onClick=${() => answer('n')}>None <kbd>n</kbd></button></div>
    <ul class="tree">${q.entries.map((e) => html`<li key=${e.rel} class=${e.on ? '' : 'off'}>
      <label style=${{ paddingLeft: `${14 + e.depth * 26}px` }}>
        <input type="checkbox" checked=${e.on} disabled=${sending} onChange=${() => answer(e.key)} />
        <span class="name">${e.name}/</span><span class="amount">${e.amount}</span></label></li>`)}</ul>
    <p class="small"><b>Selected:</b> ${q.header.main_on ? 'main folder' : html`<span class="k-warn">main folder excluded</span>`}
      + ${q.chosen} of ${q.entries.length} subfolders · ${q.scopeText}</p>
    <${Primary} question=${q} answer=${answer} sending=${sending} />
  </div>`;
}

// The browser can't hand out folder paths, so the server lists folders to choose from.
let lastFolder = '';
function FolderPicker({ question, answer, sending }) {
  const [listing, setListing] = useState(null);
  const [typed, setTyped] = useState('');
  const [error, setError] = useState('');
  const open = async (path) => {
    try {
      const data = await api(`/folders?path=${encodeURIComponent(path)}`);
      setListing(data);
      setTyped(data.path);
      lastFolder = data.path;
      setError('');
    } catch (err) {
      setError(err.message);
    }
  };
  useEffect(() => { open(lastFolder); }, []);
  const choose = (path) => answer(path);
  return html`<div class="picker">
    <form onSubmit=${(e) => { e.preventDefault(); open(typed); }}>
      <label class="small muted" for="folder-path" style=${{ flexBasis: '100%' }}>Folder</label>
      <input id="folder-path" type="text" value=${typed} spellcheck="false" onInput=${(e) => setTyped(e.target.value)} />
      <button class="btn" type="submit" disabled=${sending}>Go</button>
    </form>
    ${error && html`<p class="line k-error"><span class="mark">✗</span>${error}</p>`}
    ${listing && html`<div class="stack-tight">
      <div class="row">
        <button class="btn btn-small" disabled=${!listing.parent} onClick=${() => open(listing.parent)}>Up</button>
        <button class="btn btn-small" onClick=${() => open(listing.pictures)}>Pictures</button>
        <span class="small muted">${listing.mediaFiles} picture/video file(s) directly in this folder</span>
      </div>
      <ul class="dirs">${listing.dirs.map((d) => html`<li key=${d.path}><button disabled=${sending} onClick=${() => open(d.path)}>
        <span>${d.name}/</span>${d.protected && html`<span class="small">not scanned</span>`}</button></li>`)}
        ${!listing.dirs.length && html`<li class="muted small" style=${{ padding: '9px 14px' }}>No subfolders.</li>`}</ul>
      ${listing.truncated && html`<p class="small muted">Only the first folders are listed; type a path to go further.</p>`}
      <div class="row">
        <button class="btn btn-primary" disabled=${sending || listing.protected} onClick=${() => choose(listing.path)}>
          Scan this folder</button>
        ${listing.protected && html`<span class="small muted">The home folder, the folders above it and system folders are not scanned.</span>`}
        ${question.prompt.includes('empty to cancel') && html`<button class="btn" disabled=${sending} onClick=${() => answer('')}>Keep the current folder</button>`}
      </div>
    </div>`}
  </div>`;
}

// --- a group of pictures ----------------------------------------------------------
// One zoom level and centre for every picture of a group, so dragging or zooming
// one moves all of them and a change stands out. Kept while the same group is
// asked again (after "n", a mark, a declined deletion); the layout for every group.
const ZOOMS = [1, 2, 4, 8];
const prefs = { layout: 'side', outline: true };
let remembered = { key: '', view: null };

const clampView = ({ z, cx, cy }) => {
  const half = 0.5 / z;
  return { z, cx: Math.min(Math.max(cx, half), 1 - half), cy: Math.min(Math.max(cy, half), 1 - half) };
};
const previewSrc = (id, zoomed) => `/api/preview/${encodeURIComponent(id)}?size=${zoomed ? 'zoom' : 'screen'}`;

// A picture in a box of the group's shape, showing the part the shared view says.
function Viewport({ image, view, aspect, spot, outline, onPan, onToggle, label }) {
  const box = useRef(null);
  const drag = useRef(null);
  const [failed, setFailed] = useState(false);
  const stage = {
    width: `${view.z * 100}%`, height: `${view.z * 100}%`,
    left: `${(0.5 - view.cx * view.z) * 100}%`, top: `${(0.5 - view.cy * view.z) * 100}%`,
  };
  const down = (e) => {
    if (!onPan || e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    const d = drag.current;
    if (!d || !box.current) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    if (!d.moved) return;
    d.x = e.clientX; d.y = e.clientY;
    const rect = box.current.getBoundingClientRect();
    onPan(-dx / (rect.width * view.z), -dy / (rect.height * view.z));
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved && onToggle) onToggle();
  };
  return html`<div class=${'viewport' + (onPan ? ' pannable' : '')} ref=${box} style=${{ aspectRatio: aspect }}
      onPointerDown=${down} onPointerMove=${move} onPointerUp=${up} onPointerCancel=${() => { drag.current = null; }}>
    ${!image.preview || failed
      ? html`<span class="no-picture">${image.preview ? 'No preview: the file is gone or unreadable' : 'No preview'}</span>`
      : html`<div class="stage" style=${stage}>
          <img src=${previewSrc(image.preview, view.z > 1.5)} alt=${label} draggable="false" onError=${() => setFailed(true)} />
          ${outline && spot && html`<span class="spot" style=${{ left: `${spot[0] * 100}%`, top: `${spot[1] * 100}%`,
            width: `${spot[2] * 100}%`, height: `${spot[2] * 100}%` }}></span>
            <span class="ring" style=${{ left: `${(spot[0] + spot[2] / 2) * 100}%`, top: `${(spot[1] + spot[2] / 2) * 100}%` }}></span>`}
        </div>`}
  </div>`;
}

function GroupScreen({ question, answer, sending }) {
  const q = question;
  const g = q.group;
  const decision = q.screen === 'DecisionScreen';
  const keepIndex = decision ? 0 : g.kept || 0;
  const key = g.images.map((i) => i.name).join('\n');
  const [view, setView] = useState(remembered.key === key && remembered.view ? remembered.view : { z: 1, cx: 0.5, cy: 0.5 });
  const [layout, setLayout] = useState(prefs.layout);
  const [outline, setOutline] = useState(prefs.outline);
  const others = g.images.map((_, i) => i).filter((i) => i !== keepIndex);
  const [flipOther, setFlipOther] = useState(others.reduce((a, i) => ((g.images[i].diff ?? -1) > (g.images[a].diff ?? -1) ? i : a), others[0]));
  const [flipShowOther, setFlipShowOther] = useState(false);
  useEffect(() => { remembered = { key, view }; }, [view]);

  const first = g.images[0];
  const aspect = first.width && first.height ? `${first.width} / ${first.height}` : '4 / 3';
  const zoomTo = (z) => setView((v) => clampView({ ...v, z }));
  const pan = (dx, dy) => setView((v) => clampView({ ...v, cx: v.cx + dx, cy: v.cy + dy }));
  const toSpot = () => g.spot && setView(clampView({ z: 4, cx: g.spot[0] + g.spot[2] / 2, cy: g.spot[1] + g.spot[2] / 2 }));
  const chooseLayout = (l) => { prefs.layout = l; setLayout(l); };
  const toggleOutline = () => { prefs.outline = !outline; setOutline(!outline); };
  const flip = () => setFlipShowOther((v) => !v);
  // View buttons give the focus back, so Enter keeps answering the flow as in the terminal.
  const view_ = (fn) => (e) => { fn(); e.currentTarget.blur(); };

  // Keys of the view, besides the flow's own: Space flips, + and - zoom.
  const keys = useRef(null);
  keys.current = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || ['INPUT', 'TEXTAREA', 'BUTTON'].includes(e.target.tagName)) return;
    if (e.key === ' ' && layout === 'flip') { e.preventDefault(); flip(); }
    else if (e.key === '+') zoomTo(ZOOMS[Math.min(ZOOMS.indexOf(view.z) + 1, ZOOMS.length - 1)] || 2);
    else if (e.key === '-') zoomTo(ZOOMS[Math.max(ZOOMS.indexOf(view.z) - 1, 0)] || 1);
  };
  useEffect(() => {
    const handler = (e) => keys.current(e);
    addEventListener('keydown', handler);
    return () => removeEventListener('keydown', handler);
  }, []);

  const badgeOf = (i) => (decision ? (i === keepIndex ? ['KEEP', 'b-keep'] : ['REMOVE', 'b-remove'])
    : g.status === 'done' ? (i === keepIndex ? ['KEPT', 'b-keep'] : ['GONE', 'b-gone']) : ['KEPT', 'b-keep']);
  const diffText = (image) => (image.diff === null ? 'kept' : `diff ${image.diff}`);
  const hot = (image) => image.diff !== null && image.diff > g.borderlineAbove;

  const card = (image, i) => {
    const [text, cls] = badgeOf(i);
    return html`<article class=${'card' + (i === keepIndex ? ' keep' : '')} key=${image.name}>
      <div class="card-head"><span class="mono"><b>#${i}</b></span><span class=${'badge ' + cls}>${text}</span>
        ${decision && i === 0 && html`<span class="small muted">recommended</span>`}
        <span class=${'mono small push' + (hot(image) ? ' diff-hot' : '')}>${diffText(image)}</span></div>
      <${Viewport} image=${image} view=${view} aspect=${aspect} spot=${g.spot} outline=${outline} onPan=${pan} label=${image.name} />
      <div class="card-body">
        <div class="file">${image.name}</div>
        <div class="small muted">${image.format} · ${image.type} · ${image.width}×${image.height} · ${image.size}</div>
      </div>
      ${decision && i > 0 && html`<div class="row card-actions">
        <button class="btn btn-small" disabled=${sending} onClick=${() => answer(String(i))}>Keep this one <kbd>${i}</kbd></button>
        ${q.keys.includes('i' + i) && html`<button class="btn btn-small" disabled=${sending} onClick=${() => answer('i' + i)}>Not a duplicate <kbd>i${i}</kbd></button>`}
      </div>`}
    </article>`;
  };

  const shown = flipShowOther ? flipOther : keepIndex;
  return html`<div class="stack">
    <div class="row"><span class="mono">[${q.position}]</span><${Verdict} borderline=${g.borderline} />
      <span class="muted">worst difference <b class="mono">${g.worst}</b> of ${g.limit} allowed (0 = pixel-identical)</span>
      ${g.status === 'skipped' && decision && html`<span class="badge b-borderline">SKIPPED EARLIER</span>`}</div>

    <div class="toolbar">
      <div class="segmented" role="group" aria-label="Layout">
        <button aria-pressed=${layout === 'side'} class=${layout === 'side' ? 'on' : ''} onClick=${view_(() => chooseLayout('side'))}>Side by side</button>
        <button aria-pressed=${layout === 'flip'} class=${layout === 'flip' ? 'on' : ''} onClick=${view_(() => chooseLayout('flip'))}>Flip between two</button>
      </div>
      <div class="row" role="group" aria-label="Zoom, all pictures together">
        <span class="small muted">Zoom</span>
        ${ZOOMS.map((z) => html`<button key=${z} class=${'zoom' + (view.z === z ? ' on' : '')} aria-pressed=${view.z === z}
          onClick=${view_(() => zoomTo(z))}>${z === 1 ? 'Fit' : `${z}×`}</button>`)}
      </div>
      ${g.spot && html`<button class="btn btn-small spot-button" onClick=${view_(toSpot)}>Go to the most different square</button>`}
      ${g.spot && html`<label class="row small"><input type="checkbox" checked=${outline} onChange=${view_(toggleOutline)} /> Outline it</label>`}
      <span class="small muted push">Drag to move · + and - zoom${layout === 'flip' ? ' · Space flips' : ''}</span>
    </div>

    ${layout === 'side'
      ? html`<div class="cards">${g.images.map(card)}</div>`
      : html`<div class="flip">
          <div class="flip-main">
            <${Viewport} image=${g.images[shown]} view=${view} aspect=${aspect} spot=${g.spot} outline=${outline}
              onPan=${pan} onToggle=${flip} label=${g.images[shown].name} />
          </div>
          <div class="flip-side stack-tight">
            <span class="small muted">Showing</span>
            <div class="row"><b class="mono">#${shown}</b><span class=${'badge ' + badgeOf(shown)[1]}>${badgeOf(shown)[0]}</span>
              <span class=${'mono small' + (hot(g.images[shown]) ? ' diff-hot' : '')}>${diffText(g.images[shown])}</span></div>
            <div class="file">${g.images[shown].name}</div>
            <p class="small muted">Click the picture (or press Space) to switch between #${keepIndex} and the one chosen here.</p>
            <div class="row">${others.map((i) => html`<button key=${i} class=${'zoom' + (i === flipOther ? ' on' : '')}
              onClick=${view_(() => { setFlipOther(i); setFlipShowOther(true); })}>#${i}</button>`)}</div>
            ${decision && shown > 0 && html`<button class="btn btn-small" disabled=${sending} onClick=${() => answer(String(shown))}>
              Keep #${shown} instead <kbd>${shown}</kbd></button>`}
          </div>
        </div>`}

    ${g.spot && html`<section class="strip" aria-label="Where they differ most">
      <div class="row"><h3>Where they differ most</h3>
        <span class="small muted">The square with the largest difference, enlarged, from each picture</span></div>
      <div class="row strip-row">${g.images.map((image, i) => html`<figure key=${image.name}>
        <${Viewport} image=${image} aspect=${aspect} spot=${g.spot} outline=${true} label=${'Detail of ' + image.name}
          view=${clampView({ z: Math.min(16, 1 / (g.spot[2] * 5)), cx: g.spot[0] + g.spot[2] / 2, cy: g.spot[1] + g.spot[2] / 2 })} />
        <figcaption class="mono small">#${i} · ${diffText(image)}</figcaption></figure>`)}</div>
    </section>`}

    <details class="details" open><summary>Details</summary>
      <${VisualTable} group=${g} keepIndex=${keepIndex} done=${!decision && g.status === 'done'} /></details>
    ${q.note && html`<p class="line">${q.note}</p>`}
    <${Primary} question=${q} answer=${answer} sending=${sending} />
    <${Menu} question=${q} answer=${answer} sending=${sending} skip=${(k) => /^\d+(-\d+)?$/.test(k) || /^i\d+-i\d+$/.test(k)} />
  </div>`;
}

function RemuxView({ question, answer, sending }) {
  const q = question;
  const actions = (i) => (i === 0 ? null : html`<button class="btn btn-small" disabled=${sending} onClick=${() => answer(String(i))}>
    Keep this one <kbd>${i}</kbd></button>`);
  return html`<div class="stack">
    <div class="row"><span class="mono">[${q.number}/${q.total}]</span><span class="badge b-keep">REMUX</span>
      ${q.group.tracks === 'reordered' && html`<span class="badge b-borderline">TRACK ORDER</span>`}
      ${q.group.tracks === 'differ' && html`<span class="badge b-borderline">TRACKS DIFFER</span>`}</div>
    <p class="muted">${q.group.tracks === 'reordered' ? 'The video is identical and so are the other tracks, but in another order: a player starts with another language or sound.'
      : q.group.tracks === 'same' ? 'The video and every other track are identical.'
        : 'The video is identical; the files differ in their other tracks (sound, subtitles, ...).'}</p>
    ${q.group.tracks === 'differ' && html`<p class="alert">No file contains all tracks of the others: removing them loses a track.</p>`}
    <${ExactTable} group=${q.group} keepIndex=${0} actions=${actions} />
    <${Primary} question=${q} answer=${answer} sending=${sending} />
    <${Menu} question=${q} answer=${answer} sending=${sending} skip=${(k) => /^\d+(-\d+)?$/.test(k)} />
  </div>`;
}

function Overview({ question, answer, sending }) {
  const q = question;
  return html`<div class="stack">
    <ul class="groups">${q.groups.map((g) => {
      const [text, cls] = STATUS[g.status] || STATUS.pending;
      return html`<li key=${g.key}><button class=${String(q.current + 1) === g.key ? 'current' : ''} disabled=${sending}
        onClick=${() => answer(g.key)}>
        <span class="mono">${g.key}</span><span class=${'badge ' + cls}>${text}</span><${Verdict} borderline=${g.borderline} />
        <span class="muted small">${g.count} images · diff ${g.worst}/${g.limit}</span><span class="mono small">${g.keeper}</span>
      </button></li>`;
    })}</ul>
    <${Primary} question=${q} answer=${answer} sending=${sending} />
    <${Menu} question=${q} answer=${answer} sending=${sending} skip=${(k) => /^\d+-\d+$/.test(k)} />
  </div>`;
}

const TITLES = {
  StartScreen: 'Settings', FolderScreen: 'Choose folders to scan', DecisionScreen: 'Duplicate group',
  HandledScreen: 'Duplicate group', OverviewScreen: 'Duplicate groups overview', RemuxScreen: 'Remuxed videos',
  EndScreen: 'What next?', AfterDryRunScreen: 'Dry run finished',
};

function Question({ question, onAnswer }) {
  const [sending, setSending] = useState(false);
  // One answer per question: a second click while the first is on its way
  // (a double click) must never decide twice.
  const busy = useRef(false);
  const answer = async (value) => {
    if (busy.current) return;
    busy.current = true;
    setSending(true);
    try { await onAnswer(question.id, value); } finally { busy.current = false; setSending(false); }
  };

  // The terminal's keys: a single-letter key of the screen, y/n, Enter.
  const keys = useRef(null);
  keys.current = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || ['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
    if (question.kind === 'screen') {
      const key = e.key === 'Enter' ? '' : e.key.toLowerCase();
      if ((key.length === 1 || key === '') && question.keys.includes(key) && !(key === '' && e.target.tagName === 'BUTTON')) {
        e.preventDefault();
        answer(key);
      }
    } else if (question.kind === 'confirm') {
      if (e.key === 'y') answer(true);
      else if (e.key === 'n') answer(false);
      else if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') { e.preventDefault(); answer(question.default); }
    }
  };
  useEffect(() => {
    const handler = (e) => keys.current(e);
    addEventListener('keydown', handler);
    return () => removeEventListener('keydown', handler);
  }, []);

  const props = { question, answer, sending };
  let title = question.prompt;
  let body;
  if (question.kind === 'confirm') {
    const danger = question.tone === 'danger';
    body = html`<div class="row">
      <button class=${'btn ' + (question.default ? 'btn-primary' : '')} disabled=${sending} onClick=${() => answer(true)}>
        ${danger ? 'Yes, delete' : 'Yes'} <kbd>y</kbd></button>
      <button class=${'btn ' + (!question.default ? 'btn-primary' : '')} disabled=${sending} onClick=${() => answer(false)}>
        No <kbd>n</kbd></button></div>`;
  } else if (question.kind === 'path') {
    title = 'Choose the picture folder';
    body = html`<${FolderPicker} ...${props} />`;
  } else {
    title = TITLES[question.screen] || question.prompt;
    const view = {
      StartScreen: StartForm, FolderScreen: FolderTree, DecisionScreen: GroupScreen, HandledScreen: GroupScreen,
      RemuxScreen: RemuxView, OverviewScreen: Overview,
    }[question.screen];
    body = view ? html`<${view} ...${props} />`
      : html`<div class="stack"><${Primary} ...${props} /><${Menu} ...${props} /></div>`;
    if (question.screen === 'AfterDryRunScreen') {
      body = html`<div class="stack"><p class="row">The groups found are kept, so continuing needs no rescan. Real runs use
        the delete mode <${Mode} mode=${question.realMode} /> from your config.</p>${body}</div>`;
    }
  }
  return html`<section class=${'question' + (question.tone === 'danger' ? ' danger' : '')} aria-live="polite">
    <h2 class="prompt">${title}</h2>${body}</section>`;
}

function App() {
  const [events, setEvents] = useState([]);
  const [question, setQuestion] = useState(null);
  const [finished, setFinished] = useState(null);
  const [error, setError] = useState('');
  const [closed, setClosed] = useState(false);
  const [folder, setFolder] = useState('');
  const stick = useRef(true);
  const lastHeight = useRef(0);
  const ownScroll = useRef(false);

  useEffect(() => {
    let alive = true;
    let rev = 0;
    (async () => {
      while (alive) {
        try {
          const state = await api(`/state?since=${rev}`);
          rev = state.rev;
          if (state.events.length) {
            setEvents((list) => {
              const next = list.slice();
              for (const e of state.events) next[e.id] = e;
              return next;
            });
          }
          setQuestion(state.question);
          if (state.question && state.question.screen === 'StartScreen') setFolder(state.question.targetShort);
          setFinished(state.finished);
          setError('');
          if (state.finished !== null && !state.question) await new Promise((r) => setTimeout(r, 2000));
        } catch (err) {
          setError(err.message);
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
    })();
    const ping = setInterval(() => api('/ping', {}).catch(() => {}), 15000);
    const closing = () => navigator.sendBeacon('/api/closing', '');
    addEventListener('pagehide', closing);
    return () => { alive = false; clearInterval(ping); removeEventListener('pagehide', closing); };
  }, []);

  // Like a terminal: new content scrolls the page only for a reader who was
  // at its end; one who scrolled up to read stays where they are. Only the
  // reader's own scrolling decides (cdimport's ownScroll).
  const follow = () => {
    const height = document.documentElement.scrollHeight;
    if (height !== lastHeight.current && stick.current) {
      ownScroll.current = true;
      scrollTo(0, height);
    }
    lastHeight.current = height;
  };
  useEffect(() => {
    const onScroll = () => {
      if (ownScroll.current) { ownScroll.current = false; return; }
      stick.current = scrollY + innerHeight >= document.documentElement.scrollHeight - 40;
    };
    addEventListener('scroll', onScroll, { passive: true });
    const grows = new ResizeObserver(follow);
    grows.observe(document.body);
    return () => { removeEventListener('scroll', onScroll); grows.disconnect(); };
  }, []);
  useEffect(follow);

  const answer = async (id, value) => {
    try {
      await api('/answer', { id, value });
    } catch (err) {
      setError(err.message);
    }
  };
  // Quitting is the flow's own "q" where the question offers it, so progress
  // is saved as in the terminal; elsewhere it stops at the open question.
  const quit = async () => {
    if (question && question.kind === 'screen' && question.keys.includes('q')) {
      answer(question.id, 'q');
      return;
    }
    if (!confirm('Stop keepbest here? Saved progress is kept.')) return;
    try { await api('/quit', {}); } catch (err) { setError(err.message); }
  };
  const close = async () => {
    await api('/close', {}).catch(() => {});
    setClosed(true);
  };

  // The log grouped under the banners the flow printed.
  const sections = [];
  let step = null;
  for (const e of events) {
    if (!e) continue;
    if (e.kind === 'summary') {  // the terminal prints the summary under a banner of its own
      sections.push({ title: e.mode === 'dry_run' ? 'Summary - dry run' : 'Summary', items: [] });
      step = 'summary';
    }
    if (e.kind === 'banner' || !sections.length) {
      sections.push({ title: e.kind === 'banner' ? e.text : null, items: [] });
      if (e.kind === 'banner') { step = stepOf(e.text) || step; continue; }
    }
    sections[sections.length - 1].items.push(e);
  }
  const now = question && ['StartScreen', 'FolderScreen'].includes(question.screen) ? 'settings'
    : question && question.kind === 'path' ? null : step;
  const order = STEPS.map(([id]) => id);

  return html`
    <header class="topbar">
      <span class="brand"><img src="/static/icon.svg" alt="" width="26" height="26" /> keepbest</span>
      ${folder && html`<span class="folder">${folder}</span>`}
      <ol class="steps" aria-label="Progress">
        ${STEPS.map(([id, name]) => {
          const state = id === now && finished === null ? 'now'
            : now && (order.indexOf(id) < order.indexOf(now) || (finished !== null && id === now)) ? 'done' : '';
          return html`<li class=${'step ' + state} aria-current=${state === 'now' ? 'step' : undefined}>${name}</li>`;
        })}
      </ol>
      ${finished === null && html`<button class="btn btn-small" onClick=${quit}>Quit <kbd>q</kbd></button>`}
    </header>
    <main>
      ${sections.map((s, i) => html`<section class=${'section' + (i < sections.length - 1 ? ' old' : '')} key=${i}>
        ${s.title && html`<h2>${s.title}</h2>`}
        ${s.items.map((e) => html`<${Item} key=${e.id} event=${e} />`)}
      </section>`)}
      ${question && html`<${Question} key=${question.id} question=${question} onAnswer=${answer} />`}
      ${finished !== null && !question && html`<section class="question">
        <h2 class="prompt">${finished === 0 ? 'keepbest has finished' : finished === 130 ? 'keepbest was stopped' : `keepbest ended with exit code ${finished}`}</h2>
        <p class="muted">${finished === 0 ? 'Everything above is done.' : 'Saved progress is kept; start keepbest on the folder again to continue.'}</p>
        <div class="row"><button class="btn btn-primary" onClick=${close}>Close</button></div>
      </section>`}
    </main>
    ${error && html`<div class="toast" role="alert">${error}</div>`}
    ${closed && html`<div class="overlay"><div class="box stack-tight"><h2>keepbest has ended</h2>
      <p class="muted">You can close this tab.</p></div></div>`}
  `;
}

render(html`<${App} />`, document.getElementById('app'));
