// keepbest's page: what the flow in app.py and core_logic.py shows, and its
// open question. Everything comes from /api/state (keepbest/web.py); answers
// go back to /api/answer as the keys of the screen's choices, which are also
// its keyboard shortcuts. File names are only ever text.
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
const OPTIMIZE_STEP = ['optimize', 'media-optimizer'];  // shown once it was started from the end menu
const MODES = { trash: ['TRASH', 'b-keep'], dry_run: ['DRY RUN', 'b-plain'], permanent: ['PERMANENT', 'b-danger'] };
const STATUS = { done: ['DONE', 'b-ok'], skipped: ['SKIPPED', 'b-borderline'], ignored: ['NOT DUP', 'b-notdup'],
  pending: ['OPEN', 'b-plain'] };
const MARKS = { success: '✓', warn: '!', error: '✗' };
const URL_PATTERN = /https?:\/\/[^\s)\]]*[^\s)\].,;:!?'"]/g;

// The step a banner starts: "Stage 2 · Remuxed videos" is step 2; "Next ·
// media-optimizer" (app.OPTIMIZER_BANNER) is the program started afterwards.
function stepOf(text) {
  const stage = /^Stage (\d)/.exec(text);
  if (stage) return stage[1];
  if (/^Next · media-optimizer/.test(text)) return 'optimize';
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

// Key badges are only shown for Enter and the numbers that keep another file.
// The other keys still work: the letter a key comes from is bold in its label
// ("Skip" for s), and every button names its key on hover.
function Label({ text, k }) {
  const value = String(text);
  if (!k || !/^[a-z]$/.test(k)) return value;
  const found = new RegExp(`(^|[^\\p{L}])(${k})`, 'iu').exec(value);
  if (!found) return value;
  const at = found.index + found[1].length;
  // One span: buttons lay out their children with gaps, a word must stay whole.
  return html`<span>${value.slice(0, at)}<b class="hotkey">${value[at]}</b>${value.slice(at + 1)}</span>`;
}
const keyTitle = (k) => (k === '' ? 'Key: Enter' : k ? `Key: ${k}` : undefined);

// Buttons for a file that is still on disk: open it in a new tab (pictures the
// browser can't show are rendered), or show it in its folder. In a group's
// table (onToggle) Open shows the picture, or plays the video, below its row
// instead; on a card, which shows it already, there is no Open (noOpen).
function FileActions({ id, video = false, open = false, onToggle = null, noOpen = false }) {
  const [note, setNote] = useState('');
  if (!id) return null;
  const reveal = async (e) => {
    e.currentTarget.blur();
    try { await api('/reveal', { id }); setNote(''); } catch (err) { setNote(err.message); }
  };
  const toggle = (e) => { e.currentTarget.blur(); onToggle(); };
  return html`<span class="file-actions">
    ${noOpen ? null : onToggle
      ? html`<button class="btn btn-small" aria-pressed=${open} onClick=${toggle}
          title=${video ? (open ? 'Hide the player' : 'Play it below') : (open ? 'Hide the picture' : 'Show the picture below')}>${
            open ? 'Hide' : 'Open'}</button>`
      : html`<a class="btn btn-small" href=${`/api/file/${encodeURIComponent(id)}`} target="_blank" rel="noopener"
          title=${video ? 'Play it in a new tab' : 'Open it in a new tab'}>Open</a>`}
    <button class="btn btn-small" onClick=${reveal} title="Open its folder in the file manager">Show in folder</button>
    ${note && html`<span class="small k-error">${note}</span>`}
  </span>`;
}

// Why a file is kept: score_image's criteria in which it ranks above the others.
const CRITERIA = {
  resolution: 'larger resolution', lossless: 'lossless format', format: 'preferred format',
  name: 'name without a copy number', bpp: 'less compressed (more bits per pixel)', age: 'older file',
};
// The table cell each criterion is shown in.
const CRITERION_CELLS = { resolution: 'resolution', lossless: 'type', format: 'format', name: 'file', bpp: 'bpp' };

function reasons(group, keepIndex) {
  const by = {};
  group.images.forEach((image, i) => {
    if (i !== keepIndex && image.beaten) (by[image.beaten] = by[image.beaten] || []).push(i);
  });
  return Object.keys(CRITERIA).filter((c) => by[c]).map((c) => {
    const text = c === 'lossless' && group.images[keepIndex].jxlPreferred ? 'JPEG XL (prefer_jxl)' : CRITERIA[c];
    return { criterion: c, text, against: by[c] };
  });
}

function Why({ group, keepIndex, decided }) {
  const list = reasons(group, keepIndex);
  if (!list.length) return null;
  const all = group.images.length - 1;
  return html`<p class="why"><span class="muted">${decided ? 'Kept' : 'Recommended'} #${keepIndex} for its</span>
    ${list.map((r, i) => html`<span key=${r.criterion}>${i ? (i === list.length - 1 ? ' and ' : ', ') : ' '}<b>${r.text}</b>${
      r.against.length < all ? html` <span class="muted">(than ${r.against.map((n) => `#${n}`).join(', ')})</span>` : ''}</span>`)}<span class="muted">.</span></p>`;
}

// --- the log ----------------------------------------------------------------------
// A picture opened in its table row; a click shows it at full size in a new tab.
function InlinePicture({ image }) {
  const [failed, setFailed] = useState(false);
  if (failed) return html`<span class="small muted">No preview here. <a href=${`/api/file/${encodeURIComponent(image.file)}`}
    target="_blank" rel="noopener">Try it in a new tab</a>.</span>`;
  return html`<a href=${`/api/file/${encodeURIComponent(image.file)}`} target="_blank" rel="noopener" title="Full size in a new tab">
    <img class="inline-picture" src=${previewSrc(image.preview, false)} alt=${image.name} onError=${() => setFailed(true)} /></a>`;
}

// A table's rows whose file is open below them: [is row i open, toggle row i].
function useOpenRows() {
  const [opened, setOpened] = useState(() => new Set());
  const toggle = (i) => setOpened((now) => {
    const next = new Set(now);
    if (!next.delete(i)) next.add(i);
    return next;
  });
  return [(i) => opened.has(i), toggle];
}

function VisualTable({ group, keepIndex = 0, done = false, actions = null }) {
  // The kept file's cells that decided against another file are bold.
  const won = new Set(reasons(group, keepIndex).map((r) => CRITERION_CELLS[r.criterion]));
  const cell = (i, name, cls = '') => (i === keepIndex && won.has(name) ? `${cls} win`.trim() : cls);
  const [isOpen, toggle] = useOpenRows();
  const inline = (i) => ({ open: isOpen(i), onToggle: group.images[i].preview ? () => toggle(i) : null });
  const actionsOf = actions || ((i, opener) => html`<${FileActions} id=${group.images[i].file} ...${opener} />`);
  return html`<div class="table-box"><table>
    <thead><tr><th>#</th><th>Action</th><th>File</th><th>Format</th><th>Type</th><th class="num">Resolution</th>
      <th class="num">Size</th><th class="num">BPP</th><th class="num">Diff</th><th></th></tr></thead>
    <tbody>${group.images.map((image, i) => {
      const kept = i === keepIndex;
      const badge = done ? (kept ? ['KEPT', 'b-keep'] : ['GONE', 'b-gone']) : (kept ? ['KEEP', 'b-keep'] : ['REMOVE', 'b-remove']);
      const hot = image.diff !== null && image.diff > group.borderlineAbove;
      return html`<tr class=${kept ? 'kept' : ''} key=${i}>
        <td class="mono">${i}</td><td><span class=${'badge ' + badge[1]}>${badge[0]}</span></td>
        <td class=${cell(i, 'file', 'file')}>${image.name}</td><td class=${cell(i, 'format')}>${image.format}</td>
        <td class=${cell(i, 'type')}>${image.type}</td>
        <td class=${cell(i, 'resolution', 'num')}>${image.width}×${image.height}</td><td class="num">${image.size}</td>
        <td class=${cell(i, 'bpp', 'num')}>${image.bpp ?? ''}</td>
        <td class=${'num' + (hot ? ' diff-hot' : '')}>${image.diff === null ? '—' : image.diff}</td>
        <td class="actions"><div class="cell-actions">${actionsOf(i, inline(i))}</div></td>
      </tr>
      ${isOpen(i) && image.file && html`<tr class="picture-row" key=${'picture' + i}>
        <td></td><td colspan="9"><${InlinePicture} image=${image} /></td></tr>`}`;
    })}</tbody>
  </table></div>`;
}

// Identical files and remuxed videos; Open shows the picture or plays the video below its row.
function ExactTable({ group, keepIndex = 0 }) {
  const remux = group.kind === 'remux';
  const [isOpen, toggle] = useOpenRows();
  const columns = remux ? 6 : 4;
  return html`<div class="table-box"><table>
    <thead><tr><th>#</th><th>Action</th><th>File</th>${remux && html`<th class="num">Size</th><th>Streams</th>`}
      <th></th></tr></thead>
    <tbody>${group.files.map((file, i) => {
      const kept = keepIndex === null || i === keepIndex;
      return html`<tr class=${i === keepIndex ? 'kept' : ''} key=${i}>
        <td class="mono">${i}</td><td><span class=${'badge ' + (kept ? 'b-keep' : 'b-remove')}>${kept ? 'KEEP' : 'REMOVE'}</span></td>
        <td class="file">${file.name}</td>
        ${remux && html`<td class="num">${file.size}</td><td class="small muted">${file.streams}</td>`}
        <td class="actions"><div class="cell-actions"><${FileActions} id=${file.file} video=${remux || file.video}
          open=${isOpen(i)} onToggle=${() => toggle(i)} /></div></td>
      </tr>
      ${isOpen(i) && file.file && html`<tr class="picture-row" key=${'open' + i}><td></td><td colspan=${columns - 1}>${
        remux || file.video
          ? html`<${Player} file=${file} hint="Show in folder opens it in your file manager." />`
          : html`<${InlinePicture} image=${{ name: file.name, preview: file.file, file: file.file }} />`}</td></tr>`}`;
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

// media-optimizer's numbers (its results file, handoff.summary): tiles as in
// keepbest's own summary, a line per stage, and what both freed together.
function ToolResults({ results }) {
  return html`<div class="stack-tight">
    <dl class="tiles">${results.tiles.map(([label, value]) => html`<div class="tile" key=${label}><dt>${label}</dt><dd>${value}</dd></div>`)}</dl>
    ${results.stages.map((line) => html`<p class="line k-dim">${line}</p>`)}
    ${results.dry && html`<p class="line k-dim">A preview: nothing was converted.</p>`}
    ${results.together && html`<p class="line k-success"><span class="mark">${MARKS.success}</span>${results.together}</p>`}
  </div>`;
}

// Another program's output (media-optimizer), as in cdimport: it follows new
// lines only while scrolled to its end; Stop is Ctrl+C for that program. One
// with a page of its own (handoff) gets this tab while it runs: its page leads
// back here, and until it has ended this box links to it.
function Tool({ event }) {
  const body = useRef(null);
  const stick = useRef(true);
  const [stopping, setStopping] = useState(false);
  useEffect(() => {
    if (stick.current && body.current) body.current.scrollTop = body.current.scrollHeight;
  });
  const scrolled = (e) => { stick.current = e.target.scrollTop + e.target.clientHeight >= e.target.scrollHeight - 40; };
  const stop = async () => {
    if (!confirm(`Stop ${event.name}? It stops as on Ctrl+C and can continue next time.`)) return;
    setStopping(true);
    await api('/stop-tool', {}).catch(() => {});
  };
  const status = event.running
    ? html`<span class="row small"><span class="spinner" aria-hidden="true"></span> ${event.handoff ? 'running on its own page' : 'running'}</span>`
    : event.code === 0 ? html`<span class="small k-success">done</span>`
      : html`<span class="small k-error">${event.code === 127 ? 'could not be started' : `exit code ${event.code}`}</span>`;
  return html`<div class="tool">
    <div class="row tool-head"><b>${event.name}</b><span class="mono small muted">${event.command}</span>${status}
      ${event.running && html`<button class="btn btn-small push" disabled=${stopping} onClick=${stop}>
        ${stopping ? 'Stopping…' : `Stop ${event.name}`}</button>`}</div>
    ${event.handoff && event.running && html`<p class="line">${event.url
      ? html`It asks for its settings and shows the run on its page. <a href=${event.url}>Go to ${event.name}'s page</a>; it leads back here when done.`
      : 'Starting its page…'}</p>`}
    ${event.results && html`<${ToolResults} results=${event.results} />`}
    ${event.lines.length > 0 && (event.handoff || !event.running   // once done, its numbers come first
      ? html`<details><summary class="small muted">Its output</summary><pre class="tool-body">${event.lines.join('\n')}</pre></details>`
      : html`<pre class="tool-body" ref=${body} onScroll=${scrolled}>${event.lines.join('\n')}</pre>`)}
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
    case 'tool':
      return html`<${Tool} event=${event} />`;
    default:
      return null;
  }
}

// --- answering --------------------------------------------------------------------
// The key buttons of a screen's menu; keys handled elsewhere on the screen are
// left out, and so is a choice that only repeats the Enter button (Quit at the end).
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
      const buttons = items.filter(([key, label]) => !skip(key) && label !== question.primary)
        .flatMap(([key, label]) => expand(key).map((k) => [k, label]));
      if (!buttons.length) return null;
      return html`<div class="menu-group" key=${title}><span class="title">${title}</span><div class="row">
        ${buttons.map(([k, label]) => html`<button class="btn" key=${k} disabled=${sending} title=${keyTitle(k)}
          onClick=${() => answer(k)}><${Label} text=${label} k=${k} />${/^\d+$/.test(k) ? html` <${Key} k=${k} />` : ''}</button>`)}
      </div></div>`;
    })}
  </div>`;
}

function Primary({ question, answer, sending, danger = false }) {
  if (!question.primary) return null;
  return html`<div><button class=${'btn ' + (danger ? 'btn-danger' : 'btn-primary')} disabled=${sending}
    onClick=${() => answer('')}>${question.primary} <kbd>Enter</kbd></button></div>`;
}

// A key's label in the screen's menu.
const labelOf = (q, key) => {
  for (const [, items] of q.menu) for (const [k, label] of items) if (k === key) return label;
  return key;
};

// Where each file has its own Keep button: what Enter does, as a line instead of a button.
function EnterHint({ question }) {
  if (!question.primary) return null;
  return html`<p class="line small muted"><kbd>Enter</kbd> ${question.primary}</p>`;
}

function SettingRow({ row, set, sending }) {
  if (row.type === 'note') return html`<p class=${'note' + (row.style === 'hint' ? ' hint' : '')}>${row.text}</p>`;
  if (row.type === 'folders') {
    const subs = row.chosen === row.total ? `all ${row.total} subfolders` : `${row.chosen} of ${row.total} subfolders`;
    return html`<div class="setting"><div class="label">${row.label}</div>
      <div class="value"><span>${row.mainOn ? 'main folder' : html`<span class="k-warn">main folder excluded</span>`} + ${subs}</span>
        <button class="btn btn-small" disabled=${sending} title=${keyTitle(row.key)} onClick=${() => set(row.key)}>
          <${Label} text="Choose folders" k=${row.key} /></button></div></div>`;
  }
  const locked = row.locked !== null && row.locked !== undefined;
  return html`<div class=${'setting' + (locked ? ' locked' : '')}>
    <div class="label" title=${keyTitle(row.key)}>${row.label}</div>
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
        <button class="btn btn-small" disabled=${sending} title=${keyTitle('c')} onClick=${() => answer('c')}>
          <${Label} text="Change directory" k="c" /></button></dd>
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
      ${q.keys.includes('a') && html`<button class="btn btn-danger" disabled=${sending} title=${keyTitle('a')} onClick=${() => answer('a')}>
        ${labelOf(q, 'a')}…</button>`}
      ${q.keys.includes('n') && html`<button class="btn" disabled=${sending} title=${keyTitle('n')} onClick=${() => answer('n')}>
        Start over: discard the session</button>`}
    </div>
  </div>`;
}

// Folders opened in the tree; kept while the same tree is asked again after each change.
const opened = new Set();

function FolderTree({ question, answer, sending }) {
  const q = question;
  const [, redraw] = useState(0);
  const entries = q.entries;
  // A folder's place in the tree: its parent is the closest one above it with less depth.
  const parent = entries.map((e, i) => {
    for (let j = i - 1; j >= 0; j--) if (entries[j].depth < e.depth) return j;
    return -1;
  });
  const hasChildren = entries.map((e, i) => i + 1 < entries.length && entries[i + 1].depth > e.depth);
  const visible = (i) => parent[i] === -1 || (opened.has(entries[parent[i]].rel) && visible(parent[i]));
  // Deeper folders switched off are counted on a closed folder, so nothing is hidden unnoticed.
  const offBelow = (i) => {
    let off = 0;
    for (let j = i + 1; j < entries.length && entries[j].depth > entries[i].depth; j++) if (!entries[j].on) off++;
    return off;
  };
  const toggle = (rel) => { if (opened.has(rel)) opened.delete(rel); else opened.add(rel); redraw((n) => n + 1); };
  return html`<div class="stack">
    <p class="muted">A folder you switch off loses only its own files; you are asked whether the folders below it change too.</p>
    <label class="switch" title=${keyTitle('m')}><input type="checkbox" checked=${q.header.main_on} disabled=${sending} onChange=${() => answer('m')} />
      <span style=${{ flex: '1 1 auto' }}><b><${Label} text="Main folder" k="m" /></b> <span class="muted">· ${q.header.files_text}</span></span></label>
    <div class="row"><span class="small muted">Subfolders</span>
      <button class="btn btn-small" disabled=${sending} title=${keyTitle('a')} onClick=${() => answer('a')}><${Label} text="All" k="a" /></button>
      <button class="btn btn-small" disabled=${sending} title=${keyTitle('n')} onClick=${() => answer('n')}><${Label} text="None" k="n" /></button></div>
    <ul class="tree">${entries.map((e, i) => {
      if (!visible(i)) return null;
      const open = opened.has(e.rel);
      const hidden = hasChildren[i] && !open ? offBelow(i) : 0;
      return html`<li key=${e.rel} class=${e.on ? '' : 'off'}>
        <div class="tree-row" style=${{ paddingLeft: `${4 + e.depth * 26}px` }}>
          ${hasChildren[i]
            ? html`<button class="twisty" aria-expanded=${open} aria-label=${(open ? 'Close ' : 'Open ') + e.name}
                onClick=${(ev) => { toggle(e.rel); ev.currentTarget.blur(); }}>${open ? '▾' : '▸'}</button>`
            : html`<span class="twisty"></span>`}
          <label><input type="checkbox" checked=${e.on} disabled=${sending} onChange=${() => answer(e.key)} />
            <span class="name">${e.name}/</span>
            ${hidden > 0 && html`<span class="small k-warn">${hidden} below off</span>`}
            <span class="amount">${e.amount}</span></label></div></li>`;
    })}</ul>
    <p class="small"><b>Selected:</b> ${q.header.main_on ? 'main folder' : html`<span class="k-warn">main folder excluded</span>`}${
      ` + ${q.chosen} of ${q.entries.length} subfolders · ${q.scopeText}`}</p>
    <${Primary} question=${q} answer=${answer} sending=${sending} />
  </div>`;
}

// The browser can't hand out folder paths, so the server lists folders to choose from.
let lastFolder = '';
let lastStart = null;
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
  // The current folder; the last one listed only when the same question comes back
  // (a refused folder). The page forgets lastFolder after a reload.
  useEffect(() => {
    if (question.start !== lastStart) { lastFolder = ''; lastStart = question.start; }
    open(lastFolder || question.start || '');
  }, []);
  const choose = (path) => answer(path);
  const keep = () => { lastFolder = ''; answer(''); };
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
        ${question.prompt.includes('empty to cancel') && html`<button class="btn" disabled=${sending} onClick=${keep}>Keep the current folder</button>`}
      </div>
    </div>`}
  </div>`;
}

// --- a group of pictures ----------------------------------------------------------
// One zoom level and centre for every picture of a group, so dragging or zooming
// one moves all of them and a change stands out. Kept while the same group is
// asked again (after "n", a mark, a declined deletion); the layout for every group.
const ZOOMS = [1, 2, 4, 8];
// Pictures (and the remux player) are off until asked for, then stay on for this page.
const prefs = { layout: 'side', outline: true, pictures: false };
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
  const [pictures, setPictures] = useState(prefs.pictures);
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
  const togglePictures = () => { prefs.pictures = !pictures; setPictures(!pictures); };
  const flip = () => setFlipShowOther((v) => !v);
  // View buttons give the focus back, so Enter keeps answering the flow as in the terminal.
  const view_ = (fn) => (e) => { fn(); e.currentTarget.blur(); };

  // Keys of the view, besides the flow's own: Space flips, + and - zoom.
  const keys = useRef(null);
  keys.current = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || ['INPUT', 'TEXTAREA', 'BUTTON'].includes(e.target.tagName)) return;
    if (!pictures) return;
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
      <div class="row card-actions">${choices(i)}<${FileActions} id=${image.file} noOpen=${true} /></div>
    </article>`;
  };
  // Keeping a file, and marking one as no duplicate: on its card, in its table row and in the flip view.
  // The recommended file (#0) is kept with Enter; its border marks it. A dry run's decided group
  // (it offers "u") changes with another file's Keep, so it needs no Undo button.
  const changeable = !decision && q.keys.includes('u');
  const keepButton = (i) => {
    if (changeable && i === keepIndex) return html`<button class="btn btn-primary btn-small" disabled>Kept</button>`;
    return (decision || changeable) && html`<button class="btn btn-primary btn-small" disabled=${sending}
      title=${decision && i === 0 ? 'Keep this file and remove the others (Key: Enter)'
        : `Keep this file instead${changeable ? ': the decision changes' : ''} (Key: ${i})`}
      onClick=${() => answer(decision && i === 0 ? '' : String(i))}>Keep${decision && i === 0 ? html` <kbd>Enter</kbd>` : ''}</button>`;
  };
  const choices = (i) => html`${keepButton(i)}${decision && i > 0 && q.keys.includes('i' + i) && html`<button
    class="btn btn-small" disabled=${sending} title=${keyTitle('i' + i)} onClick=${() => answer('i' + i)}>Not a duplicate</button>`}`;
  const tableActions = (i, opener) => html`${choices(i)}<${FileActions} id=${g.images[i].file} ...${opener} />`;

  const shown = flipShowOther ? flipOther : keepIndex;
  const [number, total] = q.position.split('/');
  return html`<div class="stack">
    <nav class="crumbs" aria-label="Where you are">
      ${q.keys.includes('t') && html`<button class="btn btn-small" disabled=${sending} title="Key: t"
        onClick=${() => answer('t')}>‹ Groups overview</button><span class="muted" aria-hidden="true">›</span>`}
      <b>Group ${number} of ${total}</b></nav>
    <div class="row"><${Verdict} borderline=${g.borderline} />
      <span class="muted">worst difference <b class="mono">${g.worst}</b> of ${g.limit} allowed (0 = pixel-identical)</span>
      ${g.status === 'skipped' && decision && html`<span class="badge b-borderline">SKIPPED EARLIER</span>`}</div>

    <${Why} group=${g} keepIndex=${keepIndex} decided=${!decision && g.status === 'done'} />

    <div class="toolbar">
      <label class="row small switch-inline"><input type="checkbox" checked=${pictures} onChange=${view_(togglePictures)} />
        Show pictures</label>
      ${pictures && html`<div class="segmented" role="group" aria-label="Layout">
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
      <span class="small muted push">Drag to move · + and - zoom${layout === 'flip' ? ' · Space flips' : ''}</span>`}
    </div>

    ${!pictures ? html`<${VisualTable} group=${g} keepIndex=${keepIndex} done=${!decision && g.status === 'done'}
        actions=${tableActions} />`
      : layout === 'side'
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
            <div class="row">${keepButton(shown)}</div>
          </div>
        </div>`}

    ${pictures && g.spot && html`<section class="strip" aria-label="Where they differ most">
      <div class="row"><h3>Where they differ most</h3>
        <span class="small muted">The square with the largest difference, enlarged, from each picture</span></div>
      <div class="row strip-row">${g.images.map((image, i) => html`<figure key=${image.name}>
        <${Viewport} image=${image} aspect=${aspect} spot=${g.spot} outline=${true} label=${'Detail of ' + image.name}
          view=${clampView({ z: Math.min(16, 1 / (g.spot[2] * 5)), cx: g.spot[0] + g.spot[2] / 2, cy: g.spot[1] + g.spot[2] / 2 })} />
        <figcaption class="mono small">#${i} · ${diffText(image)}</figcaption></figure>`)}</div>
    </section>`}

    ${pictures && html`<details class="details" open><summary>Details</summary>
      <${VisualTable} group=${g} keepIndex=${keepIndex} done=${!decision && g.status === 'done'} actions=${tableActions} /></details>`}
    ${q.note && html`<p class="line">${q.note}</p>`}
    ${decision ? html`<${EnterHint} question=${q} />` : html`<${Primary} question=${q} answer=${answer} sending=${sending} />`}
    <${Menu} question=${q} answer=${answer} sending=${sending}
      skip=${(k) => /^\d+(-\d+)?$/.test(k) || /^i\d+-i\d+$/.test(k) || (changeable && k === 'u') || k === 't'} />
  </div>`;
}

// A video as the browser can play it; one that it can't (HEVC in MPEG-TS, ...) says so.
function Player({ file, hint = 'The details below still show what the file holds.' }) {
  const [failed, setFailed] = useState(false);
  if (!file.media) return null;
  return failed
    ? html`<div class="player no-play"><b>This browser can't play ${file.name}</b>
        <span class="small">${file.streams ? `${file.streams}. ` : ''}${hint}</span></div>`
    : html`<video class="player" key=${file.media} controls preload="metadata" src=${`/api/media/${encodeURIComponent(file.media)}`}
        onError=${() => setFailed(true)}></video>`;
}

const TRACK_TEXT = {
  same: 'The video and every other track are identical.',
  covered: 'The video is identical, and #0 holds every track of the others in their order.',
  reordered: 'The video is identical and so are the other tracks, but in another order: a player starts with another language or sound.',
  differ: 'The video is identical; the files differ in their other tracks (sound, subtitles, ...).',
};

function RemuxView({ question, answer, sending }) {
  const q = question;
  const g = q.group;
  const [playing, setPlaying] = useState(0);
  const [player, setPlayer] = useState(prefs.pictures);
  const togglePlayer = (e) => { prefs.pictures = !player; setPlayer(!player); e.currentTarget.blur(); };
  return html`<div class="stack">
    <div class="row"><span class="mono">[${q.number}/${q.total}]</span><span class="badge b-keep">REMUX</span>
      ${g.tracks === 'reordered' && html`<span class="badge b-borderline">TRACK ORDER</span>`}
      ${g.tracks === 'differ' && html`<span class="badge b-borderline">TRACKS DIFFER</span>`}
      ${g.tracks === 'covered' && html`<span class="badge b-ok">COVERING COPY</span>`}</div>
    <p class="muted">${TRACK_TEXT[g.tracks] || TRACK_TEXT.differ}</p>
    ${g.tracks === 'differ' && html`<p class="alert">No file contains all tracks of the others: removing them loses a track
      (other sound, language or subtitles).</p>`}
    <label class="row small switch-inline"><input type="checkbox" checked=${player} onChange=${togglePlayer} /> Show the player</label>
    <div class=${'remux' + (player ? '' : ' no-player')}>
      ${player && html`<div class="remux-player stack-tight">
        <div class="row" role="group" aria-label="File to play">${g.files.map((f, i) => html`<button key=${i}
          class=${'zoom' + (i === playing ? ' on' : '')} aria-pressed=${i === playing}
          onClick=${(e) => { setPlaying(i); e.currentTarget.blur(); }}>#${i} ${f.name}</button>`)}</div>
        <${Player} key=${playing} file=${g.files[playing]} />
      </div>`}
      <div class="remux-files stack-tight">${g.files.map((f, i) => html`<article key=${i} class=${'card' + (i === 0 ? ' keep' : '')}>
        <div class="card-head"><b class="mono">#${i}</b><span class=${'badge ' + (i === 0 ? 'b-keep' : 'b-remove')}>${i === 0 ? 'KEEP' : 'REMOVE'}</span>
          ${i === 0 && html`<span class="small muted">recommended</span>`}<span class="mono small push">${f.size}</span></div>
        <div class="card-body"><div class="file">${f.name}</div><div class="small muted">${f.streams}</div></div>
        <div class="row card-actions"><button class="btn btn-primary btn-small" disabled=${sending}
          title=${i === 0 ? 'Keep this file and remove the others (Key: Enter)' : `Keep this file instead (Key: ${i})`}
          onClick=${() => answer(i === 0 ? '' : String(i))}>Keep${i === 0 ? html` <kbd>Enter</kbd>` : ''}</button>
          <${FileActions} id=${f.file} video=${true} /></div>
      </article>`)}</div>
    </div>
    <${EnterHint} question=${q} />
    <${Menu} question=${q} answer=${answer} sending=${sending} skip=${(k) => /^\d+(-\d+)?$/.test(k)} />
  </div>`;
}

// A small picture of a group's kept file in the overview.
function Thumb({ id, label }) {
  const [failed, setFailed] = useState(false);
  return !id || failed ? html`<span class="thumb no-thumb">no preview</span>`
    : html`<img class="thumb" src=${`/api/preview/${encodeURIComponent(id)}?size=thumb`} alt=${label} loading="lazy"
        onError=${() => setFailed(true)} />`;
}

function Overview({ question, answer, sending }) {
  const q = question;
  const [filter, setFilter] = useState('all');
  const [pictures, setPictures] = useState(prefs.pictures);
  const open = (g) => g.status === 'pending' || g.status === 'skipped';
  const shown = q.groups.filter((g) => filter === 'all' || (filter === 'open' ? open(g) : !open(g)));
  const count = (st) => q.groups.filter((g) => g.status === st).length;
  return html`<div class="stack">
    <div class="row">
      <span>${`${count('done')} decided · ${count('skipped')} skipped · ${count('ignored')} not duplicates · ${count('pending')} open`}</span>
      <label class="row small switch-inline push"><input type="checkbox" checked=${pictures}
        onChange=${(e) => { prefs.pictures = !pictures; setPictures(!pictures); e.currentTarget.blur(); }} /> Show pictures</label>
      <div class="segmented" role="group" aria-label="Show">${[['all', 'All'], ['open', 'Open'], ['decided', 'Decided']].map(([id, label]) =>
        html`<button key=${id} class=${filter === id ? 'on' : ''} aria-pressed=${filter === id}
          onClick=${(e) => { setFilter(id); e.currentTarget.blur(); }}>${label}</button>`)}</div>
    </div>
    <ul class=${pictures ? 'grid' : 'grid list'}>${shown.map((g) => {
      const [text, cls] = STATUS[g.status] || STATUS.pending;
      return html`<li key=${g.key}><button class=${'tile-button' + (String(q.current + 1) === g.key ? ' current' : '')
        + (open(g) ? '' : ' closed')} disabled=${sending} title="Open this group" onClick=${() => answer(g.key)}>
        ${pictures && html`<${Thumb} id=${g.preview} label=${g.keeper} />`}
        <span class="row"><b class="mono">${g.key}</b><span class=${'badge ' + cls}>${text}</span>
          ${String(q.current + 1) === g.key && html`<span class="small" style=${{ color: 'var(--keep)' }}>▶ current</span>`}</span>
        <span class="file">${g.keeper}</span>
        <span class=${'small ' + (g.borderline ? 'diff-hot' : 'muted')}>${g.count} images · worst ${g.worst}/${g.limit}${g.borderline ? ' · borderline' : ''}</span>
      </button><div class="tile-actions"><${FileActions} id=${g.file} /></div></li>`;
    })}</ul>
    <${Primary} question=${q} answer=${answer} sending=${sending} />
    <${Menu} question=${q} answer=${answer} sending=${sending} skip=${(k) => /^\d+-\d+$/.test(k)} />
  </div>`;
}

// Screens that come back changed after most answers: shown until their next state arrives.
const FORMS = ['StartScreen', 'FolderScreen'];

const TITLES = {
  StartScreen: 'Settings', FolderScreen: 'Choose folders to scan', DecisionScreen: 'Duplicate group',
  HandledScreen: 'Duplicate group', OverviewScreen: 'Duplicate groups overview', RemuxScreen: 'Remuxed videos',
  EndScreen: 'What next?', AfterDryRunScreen: 'Dry run finished',
};

function Question({ question, onAnswer, stale = false }) {
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
    if (stale || e.ctrlKey || e.metaKey || e.altKey || ['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
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

  const props = { question, answer, sending: sending || stale };
  let title = question.prompt;
  let body;
  if (question.kind === 'confirm') {
    const danger = question.tone === 'danger';
    body = html`<div class="row">
      <button class=${'btn ' + (question.default ? 'btn-primary' : '')} disabled=${sending} title="Key: y" onClick=${() => answer(true)}>
        <${Label} text=${danger ? 'Yes, delete' : 'Yes'} k="y" />${question.default ? html` <${Key} k="" />` : ''}</button>
      <button class=${'btn ' + (!question.default ? 'btn-primary' : '')} disabled=${sending} title="Key: n" onClick=${() => answer(false)}>
        <${Label} text="No" k="n" />${!question.default ? html` <${Key} k="" />` : ''}</button></div>`;
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
  return html`<section class=${'question' + (question.tone === 'danger' ? ' danger' : '') + (stale ? ' stale' : '')}
    aria-live="polite" aria-busy=${stale}>
    <h2 class="prompt">${title}</h2>${body}</section>`;
}

// A newer keepbest, in one line under the top bar: the command, and with pipx a
// button that installs it; then keepbest starts again on the same address and
// this page loads itself again.
function UpdateNotice({ update, onError }) {
  const [hidden, setHidden] = useState(false);
  const [sending, setSending] = useState(false);
  if (!update || (hidden && update.state === 'available')) return null;
  const { version, state } = update;
  const install = async () => {
    setSending(true);
    try { await api('/update', {}); } catch (err) { onError(err.message); }
    setSending(false);
  };
  if (state === 'installing' || state === 'restarting') {
    return html`<div class="update row" role="status"><span class="spinner" aria-hidden="true"></span>
      ${state === 'installing'
        ? `Installing keepbest ${version}; this takes a minute or two, then keepbest starts again.`
        : `keepbest ${version} is installed and starts again…`}</div>`;
  }
  if (state === 'installed') {
    return html`<div class="update" role="status">keepbest ${version} is installed. Start keepbest again to use it.</div>`;
  }
  const failed = state === 'failed';
  const where = 'Possible on the start screen, the end screen or the folder question, not during a run';
  return html`<div class="update stack-tight" role="status">
    <div class="row">
      ${failed
        ? html`<span class="k-error">The update to keepbest ${version} didn't work (exit code ${update.code}).</span>`
        : html`<span><b>keepbest ${version} is available</b> <span class="muted">(this is ${update.current})</span></span>`}
      ${update.installable && html`<button class="btn btn-small" disabled=${!update.ready || sending}
        title=${update.ready ? 'Install it with pipx, then keepbest starts again here' : where}
        onClick=${install}>${failed ? 'Try again' : 'Update'}</button>`}
      <a class="small" href=${update.notes} target="_blank" rel="noopener noreferrer">What's new</a>
      ${!failed && html`<button class="btn btn-small" onClick=${() => setHidden(true)}>Not now</button>`}
      ${update.installable && !update.ready && html`<span class="small muted">Update: on the start or end screen</span>`}
    </div>
    ${failed && html`<pre class="update-command">${update.output}</pre>
      <p class="small muted">All of pipx's output is in update.log in keepbest's state folder.</p>`}
    <details class="small" open=${failed}>
      <summary>${update.installable ? 'The command, to run it in a terminal' : 'How to update'}</summary>
      <pre class="update-command">${update.command}</pre>
    </details>
  </div>`;
}

function App() {
  const [events, setEvents] = useState([]);
  const [question, setQuestion] = useState(null);
  const [finished, setFinished] = useState(null);
  const [error, setError] = useState('');
  const [closed, setClosed] = useState(false);
  const [folder, setFolder] = useState('');
  const [update, setUpdate] = useState(null);
  const stick = useRef(true);
  const lastHeight = useRef(0);
  const ownScroll = useRef(false);
  // A form (settings, folders) stays in place, dimmed, while its next state is
  // worked out (counting files), instead of vanishing and coming back.
  const lastQuestion = useRef(null);
  const lastForm = useRef(null);
  const changing = useRef(false);
  const holdLast = useRef(false);

  useEffect(() => {
    let alive = true;
    let rev = 0;
    let boot = null;
    let restarting = false;
    (async () => {
      while (alive) {
        try {
          // Updated: the old keepbest goes away, the new one answers on the same address.
          const state = await api(`/state?since=${restarting ? 0 : rev}`);
          if (boot && state.boot !== boot) { location.reload(); return; }
          boot = state.boot;
          rev = state.rev;
          restarting = !!state.update && state.update.state === 'restarting';
          setUpdate(state.update);
          if (restarting) { await new Promise((r) => setTimeout(r, 300)); continue; }
          // media-optimizer's page takes the tab: go there once; coming back,
          // the same run is not followed again (without sessionStorage, never
          // automatically, so it can't send the tab back and forth).
          for (const e of state.events) {
            if (e.kind !== 'tool' || !e.handoff || !e.url || !e.running) continue;
            const key = `keepbest-handoff-${e.id}`;
            let seen = true;
            try { seen = sessionStorage.getItem(key) === '1'; sessionStorage.setItem(key, '1'); } catch (err) { seen = true; }
            if (!seen) { location.href = e.url; return; }
          }
          if (state.events.length) {
            setEvents((list) => {
              const next = list.slice();
              for (const e of state.events) next[e.id] = e;
              return next;
            });
          }
          if (state.question) {
            lastQuestion.current = state.question;
            holdLast.current = false;
            if (FORMS.includes(state.question.screen)) lastForm.current = state.question;
          }
          setQuestion(state.question);
          if (state.question && state.question.screen === 'StartScreen') setFolder(state.question.targetShort);
          setFinished(state.finished);
          setError('');
          if (state.finished !== null && !state.question) await new Promise((r) => setTimeout(r, 2000));
        } catch (err) {
          if (restarting) { await new Promise((r) => setTimeout(r, 300)); continue; }
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
    const last = lastQuestion.current;
    // A change on a form, and the yes/no question it may lead to ("Also switch
    // the folders below?"): the form comes back, so it stays until it does.
    if (last && last.id === id) {
      if (last.kind === 'screen') changing.current = value !== '' && FORMS.includes(last.screen);
      holdLast.current = changing.current;
      if (last.kind === 'confirm' && changing.current) lastQuestion.current = lastForm.current;
    }
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
  const shown = question || (holdLast.current && finished === null ? lastQuestion.current : null);
  // The yes/no question a change on a form leads to: over the form, which stays, dimmed.
  const under = question && question.kind === 'confirm' && changing.current ? lastForm.current : null;
  const now = shown && ['StartScreen', 'FolderScreen'].includes(shown.screen) ? 'settings'
    : shown && shown.kind === 'path' ? null : step;
  const steps = events.some((e) => e && e.kind === 'banner' && stepOf(e.text) === 'optimize')
    ? [...STEPS, OPTIMIZE_STEP] : STEPS;
  const order = steps.map(([id]) => id);

  return html`
    <div class="top"><header class="topbar">
      <span class="brand"><img src="/static/icon.svg" alt="" width="26" height="26" /> keepbest</span>
      ${folder && html`<span class="folder">${folder}</span>`}
      <ol class="steps" aria-label="Progress">
        ${steps.map(([id, name]) => {
          const state = id === now && finished === null ? 'now'
            : now && (order.indexOf(id) < order.indexOf(now) || (finished !== null && id === now)) ? 'done' : '';
          return html`<li class=${'step ' + state} aria-current=${state === 'now' ? 'step' : undefined}>${name}</li>`;
        })}
      </ol>
      ${finished === null && html`<button class="btn btn-small" title="Key: q" onClick=${quit}><${Label} text="Quit" k="q" /></button>`}
    </header>
    <${UpdateNotice} update=${update} onError=${setError} /></div>
    <main>
      ${sections.map((s, i) => html`<section class=${'section' + (i < sections.length - 1 ? ' old' : '')} key=${i}>
        ${s.title && html`<h2>${s.title}</h2>`}
        ${s.items.map((e) => html`<${Item} key=${e.id} event=${e} />`)}
      </section>`)}
      ${under ? html`<${Question} key=${under.id} question=${under} onAnswer=${answer} stale=${true} />
          <div class="overlay ask"><div class="box"><${Question} key=${question.id} question=${question} onAnswer=${answer} /></div></div>`
        : shown && html`<${Question} key=${shown.id} question=${shown} onAnswer=${answer} stale=${!question} />`}
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
