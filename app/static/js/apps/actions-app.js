// "My Actions" page: grid of the current user's actions + dynamic add/edit form
// + Start / Edit / Finish / Cancel lifecycle buttons.
import { STATUS_BADGE, getJSON, sendJSON, fmtDate, fmtDuration, esc } from './actions-common.js';

const urls = document.getElementById('actions-urls').dataset;

class MyActionsManager {
  constructor() {
    this.types = [];
    this.typesById = {};
    this.grid = null;
    this.modal = null;
    this.editingId = null;
    this.currentSchema = [];

    document.getElementById('add-action-btn').addEventListener('click', () => this.openAdd());
    document.getElementById('action-save-btn').addEventListener('click', () => this.save());
    document.getElementById('action-type-select').addEventListener('change', (e) => {
      const t = this.typesById[e.target.value];
      this.renderFields(t ? t.attributes_schema : [], {});
    });

    this.init();
  }

  async init() {
    try { this.types = await getJSON(urls.urlTypes); } catch (e) { this.types = []; }
    this.typesById = Object.fromEntries(this.types.map(t => [String(t.id), t]));
    await this.loadGrid();
  }

  async loadGrid() {
    let rows = [];
    try { rows = await getJSON(urls.urlMine); } catch (e) { rows = []; }
    const options = {
      rowData: rows,
      columnDefs: this.columns(),
      defaultColDef: { resizable: true, sortable: true, filter: true, flex: 1, minWidth: 110 },
      getRowId: p => String(p.data.id),
      onCellClicked: p => this.onCell(p),
    };
    const div = document.getElementById('my-actions-grid');
    if (this.grid) this.grid.destroy();
    this.grid = agGrid.createGrid(div, options);
  }

  columns() {
    return [
      { headerName: 'Type', field: 'action_type_name', minWidth: 150 },
      { headerName: 'Subject', valueGetter: p => this.subject(p.data), minWidth: 180 },
      {
        headerName: 'Status', field: 'status', minWidth: 120, cellRenderer: p =>
          `<span class="badge bg-${STATUS_BADGE[p.value] || 'secondary'}">${esc(p.value)}</span>`,
      },
      { headerName: 'Created', field: 'created_at', valueFormatter: p => fmtDate(p.value), minWidth: 150 },
      { headerName: 'Started', field: 'started_at', valueFormatter: p => fmtDate(p.value), minWidth: 150 },
      { headerName: 'Finished', field: 'finished_at', valueFormatter: p => fmtDate(p.value), minWidth: 150 },
      { headerName: 'Duration', field: 'duration_seconds', valueFormatter: p => fmtDuration(p.value), minWidth: 110, filter: false },
      { headerName: 'Actions', field: 'actions', filter: false, sortable: false, minWidth: 210, cellRenderer: p => this.buttons(p.data.status) },
    ];
  }

  subject(row) {
    // Prefer the live type's display field so changing it updates existing rows;
    // fall back to the snapshot for retired types no longer in the active list.
    const live = this.typesById[String(row.action_type_id)];
    const df = (live && live.display_field) || (row.type_snapshot && row.type_snapshot.display_field);
    const vals = row.values || {};
    if (df && vals[df] != null && vals[df] !== '') return vals[df];
    for (const k of Object.keys(vals)) if (vals[k] != null && vals[k] !== '') return vals[k];
    return '';
  }

  buttons(status) {
    const b = (act, icon, variant, title) =>
      `<button class="btn btn-sm btn-outline-${variant} me-1" data-action="${act}" title="${title}"><i class="fas ${icon}"></i></button>`;
    let html = '<div class="btn-group" role="group">';
    if (status === 'Pending') html += b('start', 'fa-play', 'success', 'Start') + b('edit', 'fa-edit', 'primary', 'Edit') + b('cancel', 'fa-ban', 'danger', 'Cancel');
    else if (status === 'In Progress') html += b('finish', 'fa-check', 'success', 'Finish') + b('edit', 'fa-edit', 'primary', 'Edit') + b('cancel', 'fa-ban', 'danger', 'Cancel');
    else html += '<span class="text-muted small">—</span>';
    return html + '</div>';
  }

  onCell(p) {
    const btn = p.event.target.closest('button');
    if (!btn || !btn.dataset.action) return;
    const act = btn.dataset.action;
    const row = p.data;
    if (act === 'edit') return this.openEdit(row);
    if (act === 'start') return this.transition(row.id, urls.urlStart);
    if (act === 'finish') return this.transition(row.id, urls.urlFinish);
    if (act === 'cancel') { if (confirm('Cancel this action?')) this.transition(row.id, urls.urlCancel); }
  }

  async transition(id, tmpl) {
    try {
      await sendJSON(tmpl.replace('PLACEHOLDER', id), 'POST');
      await this.loadGrid();
    } catch (e) { alert(e.message); }
  }

  getModal() {
    if (!this.modal) this.modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('action-modal'));
    return this.modal;
  }

  openAdd() {
    if (!this.types.length) { alert('No active action types exist yet. Ask an admin to create one.'); return; }
    this.editingId = null;
    document.getElementById('action-modal-title').textContent = 'New Action';
    document.getElementById('action-type-select-wrap').style.display = '';
    const sel = document.getElementById('action-type-select');
    sel.innerHTML = this.types.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('');
    this.renderFields(this.types[0].attributes_schema, {});
    this.getModal().show();
  }

  openEdit(row) {
    this.editingId = row.id;
    document.getElementById('action-modal-title').textContent = 'Edit Action';
    document.getElementById('action-type-select-wrap').style.display = 'none';
    const schema = (row.type_snapshot && row.type_snapshot.attributes_schema) || [];
    this.renderFields(schema, row.values || {});
    this.getModal().show();
  }

  renderFields(schema, values) {
    this.currentSchema = schema || [];
    const container = document.getElementById('action-form-fields');
    const html = (schema || []).map(f => {
      const v = values ? values[f.key] : undefined;
      const req = f.required ? '<span class="text-danger">*</span>' : '';
      const id = `fld-${f.key}`;
      const common = `class="form-control action-field" id="${id}" data-key="${esc(f.key)}" data-type="${f.type}"`;
      let input;
      if (f.type === 'textarea') {
        input = `<textarea ${common}>${esc(v)}</textarea>`;
      } else if (f.type === 'select') {
        const opts = (f.options || []).map(o => `<option ${String(v) === String(o) ? 'selected' : ''}>${esc(o)}</option>`).join('');
        input = `<select class="form-select action-field" id="${id}" data-key="${esc(f.key)}" data-type="${f.type}">${opts}</select>`;
      } else if (f.type === 'checkbox') {
        input = `<div class="form-check form-switch"><input class="form-check-input action-field" type="checkbox" id="${id}" data-key="${esc(f.key)}" data-type="${f.type}" ${v ? 'checked' : ''}></div>`;
      } else {
        const t = f.type === 'number' ? 'number' : (f.type === 'date' ? 'date' : 'text');
        input = `<input ${common} type="${t}" value="${esc(v)}">`;
      }
      return `<div class="mb-3"><label class="form-label" for="${id}">${esc(f.label)} ${req}</label>${input}</div>`;
    }).join('');
    container.innerHTML = html || '<p class="text-muted">This action type has no fields.</p>';
    this.clearFormError();
  }

  gather() {
    const out = {};
    document.querySelectorAll('#action-form-fields .action-field').forEach(el => {
      out[el.dataset.key] = el.dataset.type === 'checkbox' ? el.checked : el.value;
    });
    return out;
  }

  showFormError(msg) {
    const el = document.getElementById('action-form-error');
    el.textContent = msg;
    el.classList.remove('d-none');
  }

  clearFormError() {
    const el = document.getElementById('action-form-error');
    el.textContent = '';
    el.classList.add('d-none');
    document.querySelectorAll('#action-form-fields .action-field.is-invalid')
      .forEach(e => e.classList.remove('is-invalid'));
  }

  // Block obviously-wrong input before it reaches the server: unparseable
  // numbers/dates (browser reports validity.badInput) and empty required fields.
  validateForm() {
    this.clearFormError();
    let firstMsg = null;
    const fields = [...document.querySelectorAll('#action-form-fields .action-field')];
    (this.currentSchema || []).forEach(f => {
      if (f.type === 'checkbox') return;
      const el = fields.find(e => e.dataset.key === f.key);
      if (!el) return;
      const badInput = (f.type === 'number' || f.type === 'date') && el.validity && el.validity.badInput;
      const emptyRequired = f.required && String(el.value ?? '').trim() === '';
      if (badInput || emptyRequired) {
        el.classList.add('is-invalid');
        if (!firstMsg) firstMsg = badInput ? `“${f.label}” must be a valid ${f.type}.` : `“${f.label}” is required.`;
      }
    });
    if (firstMsg) { this.showFormError(firstMsg); return false; }
    return true;
  }

  async save() {
    if (!this.validateForm()) return;
    const values = this.gather();
    try {
      if (this.editingId == null) {
        const typeId = parseInt(document.getElementById('action-type-select').value, 10);
        await sendJSON(urls.urlCreate, 'POST', { action_type_id: typeId, values });
      } else {
        await sendJSON(urls.urlUpdate.replace('PLACEHOLDER', this.editingId), 'PUT', { values });
      }
      this.getModal().hide();
      await this.loadGrid();
    } catch (e) { alert(e.message); }
  }
}

document.addEventListener('DOMContentLoaded', () => new MyActionsManager());
