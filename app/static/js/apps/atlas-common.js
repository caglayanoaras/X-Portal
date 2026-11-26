// Shared helpers for the Atlas pages.

export class ApiError extends Error {
  constructor(status, body) {
    let message = `Request failed (${status})`;
    if (body && typeof body.detail === 'string') message = body.detail;
    else if (body && Array.isArray(body.detail)) message = body.detail.map(d => d.msg).join(' ');
    super(message);
    this.status = status;
    this.warnings = (body && body.warnings) || [];
    this.requiresForce = !!(body && body.requires_force);
  }
}

async function answer(r) {
  if (r.status === 204) return null;
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new ApiError(r.status, data);
  return data;
}

// fetch + JSON with the portal session cookie; throws ApiError on HTTP errors.
export async function api(url, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  return answer(await fetch(url, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined }));
}

// PUT one file as multipart form data (field "file"); answers like api().
export async function upload(url, file, version) {
  const form = new FormData();
  form.append('file', file, file.name);
  if (version != null) form.append('_version', String(version));
  return answer(await fetch(url, { method: 'PUT', headers: { Accept: 'application/json' }, body: form }));
}

// POST multipart form data (a FormData); answers like api().
export async function postForm(url, form) {
  return answer(await fetch(url, { method: 'POST', headers: { Accept: 'application/json' }, body: form }));
}

// Attribute types whose value is an uploaded image or file.
export const FILE_TYPES = ['image', 'file'];

const FILE_ICONS = [
  [/\.pdf$/i, 'fa-file-pdf', '#d7140e'],
  [/\.(docx?|odt|rtf)$/i, 'fa-file-word', '#2563eb'],
  [/\.csv$/i, 'fa-file-csv', '#15803d'],
  [/\.(xlsx?|xlsm|ods)$/i, 'fa-file-excel', '#15803d'],
  [/\.(pptx?|odp)$/i, 'fa-file-powerpoint', '#c2410c'],
];

// [icon, color] for a file name: Word blue, Excel green, PowerPoint orange, PDF red.
export function fileIcon(name) {
  const hit = FILE_ICONS.find(([pattern]) => pattern.test(name || ''));
  return hit ? [hit[1], hit[2]] : ['fa-file', '#6b7280'];
}

export function fmtSize(bytes) {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// Escape text before putting it into innerHTML (values are user-supplied).
export function esc(v) {
  if (v == null) return '';
  return String(v).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

const TR = { 'İ': 'i', 'I': 'i', 'ı': 'i', 'Ş': 's', 'ş': 's', 'Ğ': 'g', 'ğ': 'g',
             'Ü': 'u', 'ü': 'u', 'Ö': 'o', 'ö': 'o', 'Ç': 'c', 'ç': 'c' };

// Case- and Turkish-accent-insensitive form, for client-side matching.
export function fold(text) {
  return String(text || '').replace(/[İIıŞşĞğÜüÖöÇç]/g, c => TR[c]).toLowerCase();
}

// "Batch/Parti No" -> "batch_parti_no" (the key rules: a-z, 0-9, single underscores).
export function slugify(text) {
  let s = fold(text).normalize('NFKD').replace(/[̀-ͯ]/g, '');
  s = s.replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  if (/^[0-9]/.test(s)) s = 'n_' + s;
  return s.slice(0, 40).replace(/_+$/, '');
}

const pad = n => String(n).padStart(2, '0');

// UTC system timestamps ("…Z") in local time.
export function fmtStamp(v) {
  if (!v) return '';
  const d = new Date(v);
  if (isNaN(d.getTime())) return String(v);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Datetime attributes are local wall-clock time already: "2026-11-06T10:30:00" -> "2026-11-06 10:30".
export function fmtLocal(v) {
  return v ? String(v).replace('T', ' ').slice(0, 16) : '';
}

// Class icons: Font Awesome 7 solid names the class builder offers.
export const ICONS = [
  // lab and measuring
  'fa-flask', 'fa-flask-vial', 'fa-vial', 'fa-vials', 'fa-microscope', 'fa-atom', 'fa-dna', 'fa-magnet',
  'fa-fire', 'fa-droplet', 'fa-temperature-half', 'fa-snowflake', 'fa-bolt', 'fa-scale-balanced',
  'fa-weight-hanging', 'fa-ruler', 'fa-ruler-combined', 'fa-gauge', 'fa-stopwatch', 'fa-hourglass-half',
  // parts, material and tools
  'fa-cube', 'fa-cubes', 'fa-box', 'fa-boxes-stacked', 'fa-box-archive', 'fa-pallet', 'fa-layer-group',
  'fa-gear', 'fa-gears', 'fa-wrench', 'fa-screwdriver-wrench', 'fa-hammer', 'fa-microchip', 'fa-plug',
  'fa-industry', 'fa-recycle', 'fa-leaf',
  // places and transport
  'fa-warehouse', 'fa-building', 'fa-house', 'fa-location-dot', 'fa-map', 'fa-globe', 'fa-plane',
  'fa-plane-up', 'fa-rocket', 'fa-helicopter', 'fa-truck', 'fa-ship', 'fa-train', 'fa-car',
  // records
  'fa-clipboard', 'fa-clipboard-list', 'fa-clipboard-check', 'fa-file', 'fa-file-lines', 'fa-file-contract',
  'fa-folder', 'fa-folder-open', 'fa-book', 'fa-book-open', 'fa-certificate', 'fa-tag', 'fa-tags',
  'fa-barcode', 'fa-qrcode', 'fa-list', 'fa-list-check', 'fa-table', 'fa-table-list', 'fa-database', 'fa-server',
  // people
  'fa-user', 'fa-users', 'fa-user-gear', 'fa-user-tie', 'fa-id-badge', 'fa-id-card',
  // time, status and the rest
  'fa-calendar', 'fa-calendar-days', 'fa-calendar-check', 'fa-clock', 'fa-chart-line', 'fa-chart-bar',
  'fa-chart-pie', 'fa-circle-check', 'fa-triangle-exclamation', 'fa-shield-halved', 'fa-star', 'fa-flag',
  'fa-bell', 'fa-link', 'fa-paperclip', 'fa-key', 'fa-lock', 'fa-eye', 'fa-award', 'fa-palette',
  'fa-puzzle-piece', 'fa-bug',
];

// Class colors the builder offers ('' is the default grey); any other hex works too.
export const COLORS = [
  ['', 'Default'], ['#21358b', 'Navy'], ['#2563eb', 'Blue'], ['#0e7490', 'Teal'], ['#15803d', 'Green'],
  ['#a16207', 'Ochre'], ['#c2410c', 'Orange'], ['#d7140e', 'Red'], ['#be185d', 'Pink'], ['#7e22ce', 'Purple'],
];
export const DEFAULT_ICON = 'fa-cube';
export const DEFAULT_COLOR = '#8a93a6';

// "fa-flask-vial" -> "flask vial"
export function iconName(icon) {
  return String(icon || '').replace(/^fa-/, '').replace(/-/g, ' ');
}

// A class's icon in its color (the defaults when it has none).
export function classIcon(icon, color, extra = '') {
  const name = /^fa-[a-z0-9-]+$/.test(icon || '') ? icon : DEFAULT_ICON;
  const tint = /^#[0-9a-f]{6}$/i.test(color || '') ? color : DEFAULT_COLOR;
  return `<i class="fas ${name} fa-fw atlas-icon ${extra}" style="--c:${tint}" aria-hidden="true"></i>`;
}

// -- the class tree ---------------------------------------------------------------
// A class's folder is a path such as "Depo/Hammadde": a folder Depo holding a folder
// Hammadde holding the class. Folders exist only through the classes in them, and
// names match without regard to case or Turkish letters (the first spelling is shown).

// {folders: Map(folded name -> folder), classes: [...]} with folder = {name, id, folders, classes}.
export function folderTree(classes) {
  const root = { folders: new Map(), classes: [] };
  for (const c of classes) {
    let node = root;
    let id = '';
    for (const part of String(c.path || '').split('/').map(s => s.trim()).filter(Boolean)) {
      const key = fold(part);
      id = id ? `${id}/${key}` : key;
      if (!node.folders.has(key)) node.folders.set(key, { name: part, id, folders: new Map(), classes: [] });
      node = node.folders.get(key);
    }
    node.classes.push(c);
  }
  return root;
}

// Every folder path that holds a class, parents included ("Depo", "Depo/Hammadde"), sorted.
export function folderPaths(classes) {
  const paths = [];
  const walk = (node, prefix) => {
    for (const f of sortedFolders(node)) {
      const path = prefix ? `${prefix}/${f.name}` : f.name;
      paths.push(path);
      walk(f, path);
    }
  };
  walk(folderTree(classes), '');
  return paths;
}

const byName = (a, b) => a.localeCompare(b, 'tr', { sensitivity: 'base' });
const sortedFolders = node => [...node.folders.values()].sort((a, b) => byName(a.name, b.name));

// Which folders this browser keeps closed (folded folder ids); every folder starts open.
const CLOSED_KEY = 'atlas.closedFolders';

function closedFolders() {
  try {
    return new Set(JSON.parse(localStorage.getItem(CLOSED_KEY) || '[]'));
  } catch (e) {
    return new Set();
  }
}

function keepClosed(id, closed) {
  const ids = closedFolders();
  if (closed) ids.add(id);
  else ids.delete(id);
  try { localStorage.setItem(CLOSED_KEY, JSON.stringify([...ids])); } catch (e) { /* storage off: not remembered */ }
}

// The sidebar: folders first, then classes, at every level. The folders around the
// active class are always open; while searching, every folder with a match is.
export function renderTree(container, classes, activeKey, href, { searching = false } = {}) {
  if (!classes.length) {
    container.innerHTML = `<div class="text-muted small px-2 py-3">${searching ? 'No class matches.' : 'No classes.'}</div>`;
    return;
  }
  const closed = searching ? new Set() : closedFolders();
  const active = classes.find(c => c.key === activeKey);
  const activeFolders = new Set();
  if (active) {
    let id = '';
    for (const part of String(active.path || '').split('/').map(s => s.trim()).filter(Boolean)) {
      id = id ? `${id}/${fold(part)}` : fold(part);
      activeFolders.add(id);
    }
  }
  const level = (node, depth) => sortedFolders(node).map(f => `
      <details class="atlas-node" data-folder="${esc(f.id)}" ${closed.has(f.id) && !activeFolders.has(f.id) ? '' : 'open'}>
        <summary class="atlas-row" style="--depth:${depth}">
          <i class="fas fa-chevron-right atlas-chevron" aria-hidden="true"></i>
          <i class="fas fa-folder fa-fw atlas-folder-icon atlas-folder-shut" aria-hidden="true"></i>
          <i class="fas fa-folder-open fa-fw atlas-folder-icon atlas-folder-opened" aria-hidden="true"></i>
          <span class="atlas-row-label">${esc(f.name)}</span>
        </summary>
        ${level(f, depth + 1)}
      </details>`).join('')
    + [...node.classes].sort((a, b) => byName(a.label, b.label)).map(c => `
      <a href="${esc(href(c))}" data-key="${esc(c.key)}" class="atlas-row ${c.key === activeKey ? 'active' : ''}"
         style="--depth:${depth}" ${c.key === activeKey ? 'aria-current="page"' : ''}>
        <span class="atlas-chevron" aria-hidden="true"></span>
        ${classIcon(c.icon, c.color)}<span class="atlas-row-label">${esc(c.label)}</span>
      </a>`).join('');
  container.innerHTML = level(folderTree(classes), 0);

  if (!container.dataset.treeBound) {        // remember what the person opens and closes
    container.dataset.treeBound = '1';
    container.addEventListener('click', e => {
      const summary = e.target.closest('summary');
      if (!summary || !container.contains(summary)) return;
      const node = summary.parentElement;
      keepClosed(node.dataset.folder, node.open);   // the click is about to flip node.open
    });
  }
}
