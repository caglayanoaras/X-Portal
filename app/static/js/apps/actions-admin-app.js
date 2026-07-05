// Actions admin page: manage the Action Type catalog (schema builder) and
// view every user's actions, with per-type attribute columns for filtering.
import { FIELD_TYPES, getJSON, sendJSON, fmtDate, fmtDuration, esc } from './actions-common.js';

const urls = document.getElementById('actions-admin-urls').dataset;


class ActionTypeManager {
  constructor(onTypesChanged) {
    this.onTypesChanged = onTypesChanged;
    this.grid = null;
    this.modal = null;
    this.delModal = null;
    this.editingId = null;
    this.deletingId = null;

    document.getElementById('add-type-btn').addEventListener('click', () => this.openAdd());
    document.getElementById('add-field-btn').addEventListener('click', () => this.addFieldRow());
    document.getElementById('type-save-btn').addEventListener('click', () => this.save());
    document.getElementById('type-delete-confirm').addEventListener('click', () => this.confirmDelete());

    this.load();
  }

  async load() {
    let rows = [];
    try { rows = await getJSON(urls.urlTypesAll); } catch (e) { rows = []; }
    const options = {
      rowData: rows,
      defaultColDef: { resizable: true, sortable: true, filter: true, flex: 1, minWidth: 120 },
      getRowId: p => String(p.data.id),
      columnDefs: [
        { headerName: 'Name', field: 'name' },
        { headerName: 'Description', field: 'description' },
        { headerName: '# Fields', valueGetter: p => (p.data.attributes_schema || []).length, maxWidth: 110, filter: false },
        { headerName: 'Expected (min)', field: 'expected_duration_minutes', maxWidth: 150, filter: false },
        {
          headerName: 'Active', field: 'is_active', maxWidth: 120, filter: false, cellRenderer: p =>
            p.value ? '<span class="text-success"><i class="fas fa-check-circle"></i></span>'
                    : '<span class="text-muted"><i class="fas fa-ban"></i> retired</span>',
        },
        {
          headerName: 'Actions', field: 'actions', filter: false, sortable: false, minWidth: 130, cellRenderer: () =>
            `<div class="btn-group">
               <button class="btn btn-sm btn-outline-primary" data-action="edit"><i class="fas fa-edit"></i></button>
               <button class="btn btn-sm btn-outline-danger" data-action="delete"><i class="fas fa-trash-alt"></i></button>
             </div>`,
        },
      ],
      onCellClicked: p => this.onCell(p),
    };
    const div = document.getElementById('action-types-grid');
    if (this.grid) this.grid.destroy();
    this.grid = agGrid.createGrid(div, options);
    if (this.onTypesChanged) this.onTypesChanged(rows);
  }

  onCell(p) {
    const btn = p.event.target.closest('button');
    if (!btn) return;
    if (btn.dataset.action === 'edit') this.openEdit(p.data);
    if (btn.dataset.action === 'delete') this.openDelete(p.data);
  }

  getModal() { if (!this.modal) this.modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('type-modal')); return this.modal; }
  getDelModal() { if (!this.delModal) this.delModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('type-delete-modal')); return this.delModal; }

  openAdd() {
    this.editingId = null;
    document.getElementById('type-modal-title').textContent = 'New Action Type';
    document.getElementById('type-form').reset();
    document.getElementById('type-active').checked = true;
    document.getElementById('schema-builder').innerHTML = '';
    this.refreshDisplayFieldOptions();
    this.getModal().show();
  }

  openEdit(row) {
    this.editingId = row.id;
    document.getElementById('type-modal-title').textContent = 'Edit Action Type';
    document.getElementById('type-name').value = row.name || '';
    document.getElementById('type-description').value = row.description || '';
    document.getElementById('type-expected').value = row.expected_duration_minutes != null ? row.expected_duration_minutes : '';
    document.getElementById('type-active').checked = !!row.is_active;
    document.getElementById('schema-builder').innerHTML = '';
    (row.attributes_schema || []).forEach(f => this.addFieldRow(f));
    this.refreshDisplayFieldOptions(row.display_field);
    this.getModal().show();
  }

  addFieldRow(f = {}) {
    const wrap = document.createElement('div');
    wrap.className = 'row g-2 align-items-end mb-2 schema-row border-bottom pb-2';
    wrap.innerHTML = `
      <div class="col-md-3"><label class="form-label small mb-0">Label</label>
        <input class="form-control form-control-sm f-label" value="${esc(f.label)}"></div>
      <div class="col-md-2"><label class="form-label small mb-0">Key</label>
        <input class="form-control form-control-sm f-key" value="${esc(f.key)}"></div>
      <div class="col-md-2"><label class="form-label small mb-0">Type</label>
        <select class="form-select form-select-sm f-type">
          ${FIELD_TYPES.map(t => `<option ${f.type === t ? 'selected' : ''}>${t}</option>`).join('')}
        </select></div>
      <div class="col-md-2"><label class="form-label small mb-0">Options</label>
        <input class="form-control form-control-sm f-options" placeholder="Low, High" value="${esc((f.options || []).join(', '))}"></div>
      <div class="col-md-2"><label class="form-label small mb-0 d-block">Required</label>
        <div class="form-check form-switch"><input class="form-check-input f-required" type="checkbox" ${f.required ? 'checked' : ''}></div></div>
      <div class="col-md-1"><label class="form-label small mb-0 d-block">&nbsp;</label>
        <button type="button" class="btn btn-sm btn-outline-danger f-remove w-100" title="Remove field"><i class="fas fa-times"></i></button></div>`;

    const refresh = () => this.refreshDisplayFieldOptions(document.getElementById('type-display-field').value);
    wrap.querySelector('.f-remove').addEventListener('click', () => { wrap.remove(); refresh(); this.validateKeys(); });
    wrap.querySelector('.f-label').addEventListener('input', refresh);
    wrap.querySelector('.f-key').addEventListener('input', () => { refresh(); this.validateKeys(); });
    document.getElementById('schema-builder').appendChild(wrap);
  }

  // Highlight duplicate keys inline as the admin types (keys must be unique).
  validateKeys() {
    const inputs = [...document.querySelectorAll('#schema-builder .f-key')];
    const counts = {};
    inputs.forEach(i => { const k = i.value.trim(); if (k) counts[k] = (counts[k] || 0) + 1; });
    let ok = true;
    inputs.forEach(i => {
      const dup = !!i.value.trim() && counts[i.value.trim()] > 1;
      i.classList.toggle('is-invalid', dup);
      if (dup) ok = false;
    });
    return ok;
  }

  collectSchema() {
    return [...document.querySelectorAll('#schema-builder .schema-row')].map(r => {
      const type = r.querySelector('.f-type').value;
      const options = r.querySelector('.f-options').value.split(',').map(s => s.trim()).filter(Boolean);
      return {
        label: r.querySelector('.f-label').value.trim(),
        key: r.querySelector('.f-key').value.trim(),
        type,
        required: r.querySelector('.f-required').checked,
        options: type === 'select' ? options : null,
      };
    });
  }

  refreshDisplayFieldOptions(selected) {
    const schema = this.collectSchema().filter(f => f.key);
    const sel = document.getElementById('type-display-field');
    sel.innerHTML = '<option value="">— none —</option>' +
      schema.map(f => `<option value="${esc(f.key)}" ${f.key === selected ? 'selected' : ''}>${esc(f.label || f.key)}</option>`).join('');
  }

  async save() {
    const payload = {
      name: document.getElementById('type-name').value.trim(),
      description: document.getElementById('type-description').value.trim() || null,
      expected_duration_minutes: document.getElementById('type-expected').value
        ? parseInt(document.getElementById('type-expected').value, 10) : null,
      is_active: document.getElementById('type-active').checked,
      display_field: document.getElementById('type-display-field').value || null,
      attributes_schema: this.collectSchema(),
    };
    if (!payload.name) { alert('Name is required'); return; }
    if (!this.validateKeys()) { alert('Two attributes share the same key — keys must be unique.'); return; }
    try {
      if (this.editingId == null) await sendJSON(urls.urlTypeCreate, 'POST', payload);
      else await sendJSON(urls.urlTypeUpdate.replace('PLACEHOLDER', this.editingId), 'PUT', payload);
      this.getModal().hide();
      await this.load();
    } catch (e) { alert(e.message); }
  }

  openDelete(row) { this.deletingId = row.id; this.getDelModal().show(); }

  async confirmDelete() {
    try {
      await sendJSON(urls.urlTypeDelete.replace('PLACEHOLDER', this.deletingId), 'DELETE');
      this.getDelModal().hide();
      await this.load();
    } catch (e) { alert(e.message); }
  }
}


class AllActionsManager {
  constructor() {
    this.grid = null;
    this.typesById = {};
    this.filter = document.getElementById('all-actions-type-filter');
    this.filter.addEventListener('change', () => this.load());
    this.load();
  }

  setTypes(types) {
    this.typesById = Object.fromEntries(types.map(t => [String(t.id), t]));
    const current = this.filter.value;
    this.filter.innerHTML = '<option value="">All types</option>' +
      types.map(t => `<option value="${t.id}">${esc(t.name)}${t.is_active ? '' : ' (retired)'}</option>`).join('');
    if (current) this.filter.value = current;
  }

  async load() {
    const typeId = this.filter.value;
    let url = urls.urlAllActions;
    if (typeId) url += `?action_type_id=${typeId}`;
    let rows = [];
    try { rows = await getJSON(url); } catch (e) { rows = []; }
    const options = {
      rowData: rows,
      defaultColDef: { resizable: true, sortable: true, filter: true, flex: 1, minWidth: 120, enableRowGroup: true },
      rowGroupPanelShow: 'always',
      getRowId: p => String(p.data.id),
      columnDefs: this.columns(typeId),
    };
    const div = document.getElementById('all-actions-grid');
    if (this.grid) this.grid.destroy();
    this.grid = agGrid.createGrid(div, options);
  }

  subject(row) {
    const live = this.typesById[String(row.action_type_id)];
    const df = (live && live.display_field) || (row.type_snapshot && row.type_snapshot.display_field);
    const vals = row.values || {};
    if (df && vals[df] != null && vals[df] !== '') return vals[df];
    for (const k of Object.keys(vals)) if (vals[k] != null && vals[k] !== '') return vals[k];
    return '';
  }

  columns(typeId) {
    const cols = [
      { headerName: 'User', field: 'user_fullname', minWidth: 150 },
      { headerName: 'Type', field: 'action_type_name', minWidth: 140 },
      { headerName: 'Subject', valueGetter: p => this.subject(p.data), minWidth: 170 },
      { headerName: 'Status', field: 'status', minWidth: 120 },
      { headerName: 'Created', field: 'created_at', valueFormatter: p => fmtDate(p.value), minWidth: 150 },
      { headerName: 'Started', field: 'started_at', valueFormatter: p => fmtDate(p.value), minWidth: 150 },
      { headerName: 'Finished', field: 'finished_at', valueFormatter: p => fmtDate(p.value), minWidth: 150 },
      { headerName: 'Duration', field: 'duration_seconds', valueFormatter: p => fmtDuration(p.value), aggFunc: 'sum', minWidth: 120, filter: 'agNumberColumnFilter' },
    ];
    // When a single type is selected, expose its attributes as filterable columns
    // (this is the "filter within a chosen action type" the boss asked for).
    if (typeId && this.typesById[typeId]) {
      const schema = this.typesById[typeId].attributes_schema || [];
      schema.slice().reverse().forEach(f => {
        cols.splice(3, 0, {   // right after the Subject column
          headerName: f.label,
          colId: `attr_${f.key}`,
          valueGetter: p => (p.data && p.data.values) ? p.data.values[f.key] : null,
          filter: f.type === 'number' ? 'agNumberColumnFilter' : 'agTextColumnFilter',
          minWidth: 150,
        });
      });
    }
    return cols;
  }
}


// Permissions tab (superadmin only): choose which roles may open the admin area.
class PermissionsManager {
  constructor() {
    this.url = urls.urlAdminRoles;
    document.getElementById('perm-save-btn').addEventListener('click', () => this.save());
    this.load();
  }

  async load() {
    const list = document.getElementById('perm-roles-list');
    let data;
    try { data = await getJSON(this.url); }
    catch (e) { list.innerHTML = '<div class="text-danger">Failed to load roles.</div>'; return; }

    const selected = new Set(data.admin_role_ids || []);
    if (!data.roles.length) {
      list.innerHTML = '<div class="text-muted">No roles exist yet. Create roles in Users &amp; Permissions first.</div>';
      return;
    }
    list.innerHTML = data.roles.map(r => `
      <div class="form-check">
        <input class="form-check-input perm-role" type="checkbox" value="${r.id}" id="perm-role-${r.id}" ${selected.has(r.id) ? 'checked' : ''}>
        <label class="form-check-label" for="perm-role-${r.id}">${esc(r.rolename)}</label>
      </div>`).join('');
  }

  async save() {
    const roleIds = [...document.querySelectorAll('.perm-role:checked')].map(c => parseInt(c.value, 10));
    try {
      await sendJSON(this.url, 'PUT', { role_ids: roleIds });
      const status = document.getElementById('perm-save-status');
      status.textContent = 'Saved.';
      setTimeout(() => { status.textContent = ''; }, 2500);
    } catch (e) { alert(e.message); }
  }
}


document.addEventListener('DOMContentLoaded', () => {
  const allActions = new AllActionsManager();
  // ActionTypeManager owns the authoritative type list; share it with the
  // All Actions view so the type filter and attribute columns stay in sync.
  new ActionTypeManager(types => allActions.setTypes(types));
  // Only superadmins get the Permissions tab in the DOM.
  if (document.getElementById('tab-perms')) new PermissionsManager();
});
