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

// fetch + JSON with the portal session cookie; throws ApiError on HTTP errors.
export async function api(url, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const r = await fetch(url, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  if (r.status === 204) return null;
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new ApiError(r.status, data);
  return data;
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
        ${esc(c.label)}
      </a>`).join('')}`).join('');
}
