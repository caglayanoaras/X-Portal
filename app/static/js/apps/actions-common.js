// Shared helpers for the Actions module pages.

export const FIELD_TYPES = ['text', 'number', 'date', 'select', 'textarea', 'checkbox'];

export const STATUS_BADGE = {
  'Pending': 'secondary',
  'In Progress': 'info',
  'Completed': 'success',
  'Cancelled': 'dark',
};

export async function getJSON(url) {
  const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function sendJSON(url, method, body) {
  const r = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    body: body != null ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail = 'Request failed';
    try { detail = (JSON.parse(await r.text()).detail) || detail; } catch (e) { /* keep default */ }
    throw new Error(detail);
  }
  return r.status === 204 ? null : r.json();
}

export function fmtDate(v) {
  if (!v) return '';
  const d = new Date(v);
  return isNaN(d.getTime()) ? '' : d.toLocaleString();
}

export function fmtDuration(s) {
  if (s == null) return '';
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? `${h}h ` : '') + (m ? `${m}m ` : '') + `${sec}s`;
}

// Escape text before injecting into innerHTML (values are user-supplied).
export function esc(v) {
  if (v == null) return '';
  return String(v).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
