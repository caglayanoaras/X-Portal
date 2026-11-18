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

// [{path, classes}] with top-level classes first, then folders by name.
export function groupByPath(classes) {
  const groups = new Map();
  for (const c of classes) {
    if (!groups.has(c.path)) groups.set(c.path, []);
    groups.get(c.path).push(c);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b, 'tr')))
    .map(([path, list]) => ({ path, classes: list.sort((x, y) => x.label.localeCompare(y.label, 'tr')) }));
}

// The class list in the sidebar (folders as small headings).
export function renderTree(container, classes, activeKey, href) {
  const groups = groupByPath(classes);
  if (!groups.length) {
    container.innerHTML = '<div class="text-muted small px-2 py-3">No classes.</div>';
    return;
  }
  container.innerHTML = groups.map(g => `
    ${g.path ? `<div class="atlas-folder"><i class="fas fa-folder me-1"></i>${esc(g.path.split('/').join(' / '))}</div>` : ''}
    ${g.classes.map(c => `
      <a href="${esc(href(c))}" data-key="${esc(c.key)}"
         class="list-group-item list-group-item-action ${g.path ? 'nested' : ''} ${c.key === activeKey ? 'active' : ''}">
        ${classIcon(c.icon, c.color)}<span>${esc(c.label)}</span>
      </a>`).join('')}`).join('');
}
