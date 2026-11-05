// Atlas: browse a class's objects in a grid; view, add, edit and delete them.
// The grid pages, sorts and filters on the server (AG Grid infinite row model),
// and the form is built from the class definition. URLs: #class or #class/id.
import { api, esc, fmtLocal, fmtStamp, fold, renderTree } from './atlas-common.js';

const root = document.getElementById('atlas-app');
const URLS = { classes: root.dataset.urlClasses, roles: root.dataset.urlRoles, admin: root.dataset.urlAdmin };
const IS_ADMIN = root.dataset.isAdmin === 'true';
const PAGE_SIZE = 100;
const $ = id => document.getElementById(id);

const TEXT_FILTER = { filterOptions: ['contains', 'equals', 'notEqual', 'startsWith', 'blank', 'notBlank'], maxNumConditions: 1 };
const NUMBER_FILTER = {
  filterOptions: ['equals', 'notEqual', 'greaterThan', 'greaterThanOrEqual', 'lessThan', 'lessThanOrEqual',
                  'inRange', 'blank', 'notBlank'],
  maxNumConditions: 1,
};
const DATE_FILTER = { filterOptions: ['equals', 'greaterThan', 'lessThan', 'inRange', 'blank', 'notBlank'], maxNumConditions: 1 };
const LINK_FILTER = { filterOptions: ['contains', 'blank', 'notBlank'], maxNumConditions: 1 };
// AG Grid filter types -> API filter operators ('' = equals)
const TEXT_OPS = { contains: 'contains', equals: '', notEqual: 'ne', startsWith: 'startswith' };
const NUMBER_OPS = { equals: '', notEqual: 'ne', greaterThan: 'gt', greaterThanOrEqual: 'gte', lessThan: 'lt', lessThanOrEqual: 'lte' };

function nextDay(day) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

function fmtValue(type, value) {
  if (value == null) return '';
  if (Array.isArray(value)) return value.map(v => fmtValue(type, v)).filter(Boolean).join(', ');
  if (type === 'boolean') return value ? 'Yes' : 'No';
  if (type === 'datetime') return fmtLocal(value);
  return String(value);
}

class AtlasBrowser {
  constructor() {
    this.classes = [];
    this.roles = [];
    this.roleNames = {};
    this.cls = null;          // the open class's definition
    this.grid = null;
    this.kinds = {};          // grid column -> how its filter maps to the API
    this.search = '';
    this.editing = null;      // the object in the form; null for a new one
    this.selects = {};        // tom-select instances in the form
    this.pendingRoute = false;
    this.modal = bootstrap.Modal.getOrCreateInstance($('atlas-object-modal'));
    this.bind();
    this.init();
  }

  bind() {
    $('atlas-tree-filter').addEventListener('input', () => this.renderTree());
    let timer;
    $('atlas-search').addEventListener('input', e => {
      clearTimeout(timer);
      timer = setTimeout(() => { this.search = e.target.value.trim(); this.reloadGrid(); }, 300);
    });
    $('atlas-new').addEventListener('click', () => this.openForm(null));
    $('atlas-object-save').addEventListener('click', () => this.save());
    $('atlas-object-delete').addEventListener('click', () => { if (this.editing) this.remove(this.editing._id); });
    $('atlas-object-modal').addEventListener('hidden.bs.modal', () => {
      for (const select of Object.values(this.selects)) select.destroy();
      this.selects = {};
      this.editing = null;
      if (this.pendingRoute) {
        this.pendingRoute = false;
        this.route();
      } else if (this.cls) {
        history.replaceState(null, '', `#${this.cls.key}`);
      }
    });
    window.addEventListener('hashchange', () => this.route());
  }

  async init() {
    try {
      [this.classes, this.roles] = await Promise.all([api(URLS.classes), api(URLS.roles)]);
    } catch (e) {
      this.message(esc(e.message));
      return;
    }
    this.roleNames = Object.fromEntries(this.roles.map(r => [r.id, r.rolename]));
    this.renderTree();
    this.route();
  }

  message(html) {
    $('atlas-class').classList.add('d-none');
    $('atlas-message').classList.remove('d-none');
    $('atlas-message').innerHTML = html;
  }

  renderTree() {
    const filter = fold($('atlas-tree-filter').value.trim());
    const shown = this.classes.filter(c => !filter || fold(`${c.label} ${c.path}`).includes(filter));
    renderTree($('atlas-tree'), shown, this.cls && this.cls.key, c => `#${c.key}`);
  }

  // #class or #class/id
  async route() {
    if ($('atlas-object-modal').classList.contains('show')) {
      this.pendingRoute = true;          // finish closing the form first
      this.modal.hide();
      return;
    }
    const [key, id] = decodeURIComponent(location.hash.slice(1)).split('/');
    if (!key) {
      this.cls = null;
      this.renderTree();
      if (this.classes.length) this.message('Choose a class on the left.');
      else if (IS_ADMIN) this.message(`Atlas has no classes yet. <a href="${esc(URLS.admin)}">Create the first one</a>.`);
      else this.message('There are no classes you can see yet.');
      return;
    }
    try {
      if (!this.cls || this.cls.key !== key) await this.openClass(key);
      if (id) await this.openForm(Number(id));
    } catch (e) {
      this.cls = null;
      this.renderTree();
      this.message(esc(e.message));
    }
  }

  async openClass(key) {
    this.cls = null;
    this.cls = await api(`${URLS.classes}/${encodeURIComponent(key)}`);
    this.renderTree();
    $('atlas-message').classList.add('d-none');
    $('atlas-class').classList.remove('d-none');
    $('atlas-class-label').textContent = this.cls.label;
    $('atlas-class-description').textContent = this.cls.description || '';
    $('atlas-new').classList.toggle('d-none', !this.cls.permissions.create);
    $('atlas-search').value = '';
    $('atlas-grid-error').classList.add('d-none');
    this.search = '';
    this.buildGrid();
  }

  objectsUrl(key) {
    return `${URLS.classes}/${encodeURIComponent(key || this.cls.key)}/objects`;
  }

  // -- grid ----------------------------------------------------------------------
  buildGrid() {
    if (this.grid) this.grid.destroy();
    this.kinds = {};
    this.grid = agGrid.createGrid($('atlas-grid'), {
      columnDefs: this.columns(),
      rowModelType: 'infinite',
      datasource: this.datasource(),
      pagination: true,
      paginationPageSize: PAGE_SIZE,
      paginationPageSizeSelector: false,
      cacheBlockSize: PAGE_SIZE,
      maxBlocksInCache: 10,
      getRowId: p => String(p.data._id),
      // Filters live in the side bar (as on the other portal grids), so headers keep room for labels.
      defaultColDef: { resizable: true, sortable: true, flex: 1, minWidth: 130, suppressHeaderFilterButton: true,
                       wrapHeaderText: true, autoHeaderHeight: true },
      sideBar: { toolPanels: ['filters', 'columns'], defaultToolPanel: null },
      onCellClicked: p => this.onCell(p),
      onRowDoubleClicked: p => { if (p.data) this.go(p.data._id); },
    });
    $('atlas-grid').__agGridInstance = this.grid;   // same handle the other portal grids keep
  }

  reloadGrid() {
    if (this.grid) this.grid.setGridOption('datasource', this.datasource());
  }

  refreshGrid() {
    if (this.grid) this.grid.refreshInfiniteCache();
  }

  columns() {
    const c = this.cls;
    const cols = [{ field: '_id', headerName: 'ID', sort: 'desc', minWidth: 80, maxWidth: 100,
                    filter: 'agNumberColumnFilter', filterParams: NUMBER_FILTER }];
    this.kinds._id = 'number';
    for (const a of c.attributes.filter(a => a.show)) cols.push(this.attributeColumn(a));
    for (const r of c.relationships.filter(r => r.show)) {
      this.kinds[r.key] = 'link';
      cols.push({ field: r.key, headerName: r.label, filter: 'agTextColumnFilter', filterParams: LINK_FILTER,
                  valueFormatter: p => this.linkText(p.value) });
    }
    if (IS_ADMIN) {
      cols.push({ field: '_roles', headerName: 'Roles', sortable: false, filter: false,
                  valueFormatter: p => (p.value || []).map(id => this.roleNames[id] || `#${id}`).join(', ') });
    }
    for (const [field, headerName, hide] of [['_updated_at', 'Updated', false], ['_updated_by', 'Updated by', false],
                                             ['_created_at', 'Created', true], ['_created_by', 'Created by', true]]) {
      const stamp = field.endsWith('_at');
      this.kinds[field] = stamp ? 'timestamp' : 'text';
      cols.push({ field, headerName, hide, filter: stamp ? 'agDateColumnFilter' : 'agTextColumnFilter',
                  filterParams: stamp ? DATE_FILTER : TEXT_FILTER,
                  valueFormatter: stamp ? (p => fmtStamp(p.value)) : undefined });
    }
    cols.push({ colId: '_actions', headerName: '', sortable: false, filter: false, resizable: false,
                suppressHeaderMenuButton: true, pinned: 'right', width: 112, minWidth: 112, maxWidth: 112, flex: 0,
                cellRenderer: p => this.actionButtons(p.data) });
    return cols;
  }

  attributeColumn(a) {
    const type = a.type === 'lookthrough' ? a.value_type : a.type;
    const col = { field: a.key, headerName: a.label, valueFormatter: p => fmtValue(type, p.value) };
    if (type === 'int' || type === 'float') {
      Object.assign(col, { filter: 'agNumberColumnFilter', filterParams: NUMBER_FILTER, type: 'rightAligned' });
      this.kinds[a.key] = 'number';
    } else if (type === 'date' || type === 'datetime') {
      Object.assign(col, { filter: 'agDateColumnFilter', filterParams: DATE_FILTER });
      this.kinds[a.key] = type;
    } else if (type === 'boolean') {
      Object.assign(col, { filter: 'agSetColumnFilter', filterParams: {
        values: ['true', 'false'], valueFormatter: p => ({ true: 'Yes', false: 'No' }[p.value] || p.value) } });
      if (a.type === 'boolean') {
        col.cellRenderer = p => (p.value == null ? '' : p.value
          ? '<i class="fas fa-check text-success"></i>' : '<span class="text-muted">No</span>');
      }
      this.kinds[a.key] = 'set';
    } else if (type === 'choice') {
      Object.assign(col, { filter: 'agSetColumnFilter', filterParams: { values: a.options || [] } });
      this.kinds[a.key] = 'set';
    } else {
      Object.assign(col, { filter: 'agTextColumnFilter', filterParams: TEXT_FILTER });
      if (type === 'text') col.minWidth = 220;
      this.kinds[a.key] = 'text';
    }
    return col;
  }

  linkText(value) {
    if (value == null) return '';
    return (Array.isArray(value) ? value : [value]).map(v => (v && typeof v === 'object' ? v._title : `#${v}`)).join(', ');
  }

  actionButtons(data) {
    if (!data) return '';
    const can = data._can || {};
    return `<div class="btn-group btn-group-sm">
      <button type="button" class="btn btn-outline-primary" data-action="open" title="${can.update ? 'Edit' : 'View'}">
        <i class="fas ${can.update ? 'fa-pen' : 'fa-eye'}"></i></button>
      ${can.delete ? `<button type="button" class="btn btn-outline-danger" data-action="delete" title="Delete">
        <i class="fas fa-trash"></i></button>` : ''}
    </div>`;
  }

  onCell(p) {
    const button = p.event && p.event.target.closest('button[data-action]');
    if (!button || !p.data) return;
    if (button.dataset.action === 'open') this.go(p.data._id);
    if (button.dataset.action === 'delete') this.remove(p.data._id);
  }

  go(id) {
    location.hash = `${this.cls.key}/${id}`;
  }

  datasource() {
    return {
      getRows: async params => {
        const size = params.endRow - params.startRow;
        const qs = new URLSearchParams({ page: Math.floor(params.startRow / size) + 1, page_size: size, expand: '*' });
        if (this.search) qs.set('q', this.search);
        const sort = params.sortModel.map(s => (s.sort === 'desc' ? '-' : '') + s.colId).join(',');
        if (sort) qs.set('sort', sort);
        this.addFilters(qs, params.filterModel);
        try {
          const result = await api(`${this.objectsUrl()}?${qs}`);
          $('atlas-grid-error').classList.add('d-none');
          params.successCallback(result.items, result.total);
        } catch (e) {
          params.failCallback();
          this.notice(e.message);
        }
      },
    };
  }

  addFilters(qs, model) {
    for (const [col, f] of Object.entries(model || {})) {
      const kind = this.kinds[col] || 'text';
      if (f.filterType === 'set') {
        qs.append(`${col}__in`, (f.values || []).join(','));
      } else if (f.type === 'blank' || f.type === 'notBlank') {
        qs.append(`${col}__isnull`, f.type === 'blank' ? 'true' : 'false');
      } else if (f.filterType === 'text' && f.type in TEXT_OPS) {
        const op = TEXT_OPS[f.type];
        qs.append(op ? `${col}__${op}` : col, f.filter ?? '');
      } else if (f.filterType === 'number') {
        if (f.type === 'inRange') {
          qs.append(`${col}__gte`, f.filter);
          qs.append(`${col}__lte`, f.filterTo);
        } else if (f.type in NUMBER_OPS) {
          const op = NUMBER_OPS[f.type];
          qs.append(op ? `${col}__${op}` : col, f.filter);
        }
      } else if (f.filterType === 'date') {
        const from = (f.dateFrom || '').slice(0, 10);
        const to = (f.dateTo || '').slice(0, 10);
        const exact = kind === 'date';     // datetimes and timestamps also carry a time
        if (f.type === 'equals') qs.append(`${col}__on`, from);
        else if (f.type === 'greaterThan') qs.append(`${col}__${exact ? 'gt' : 'gte'}`, exact ? from : nextDay(from));
        else if (f.type === 'lessThan') qs.append(`${col}__lt`, from);
        else if (f.type === 'inRange') {
          qs.append(`${col}__gte`, from);
          qs.append(`${col}__${exact ? 'lte' : 'lt'}`, exact ? to : nextDay(to));
        }
      }
    }
  }

  notice(message) {
    const box = $('atlas-grid-error');
    box.textContent = message;
    box.classList.remove('d-none');
  }

  // -- form ----------------------------------------------------------------------
  async openForm(id) {
    let obj = null;
    if (id != null) {
      try {
        obj = await api(`${this.objectsUrl()}/${id}?expand=*`);
      } catch (e) {
        this.notice(e.message);
        history.replaceState(null, '', `#${this.cls.key}`);
        return;
      }
    }
    this.editing = obj;
    this.renderForm(obj);
    this.modal.show();
  }

  renderForm(obj) {
    const c = this.cls;
    const isNew = !obj;
    const canEdit = isNew ? c.permissions.create : !!(obj._can && obj._can.update);
    this.clearError();
    $('atlas-object-title').textContent = isNew ? `New · ${c.label}` : `${c.label} · ${obj._title}`;

    const fields = [];
    for (const a of c.attributes) {
      if (a.type === 'lookthrough') {
        if (!isNew) fields.push(this.computedField(a, obj[a.key]));
      } else {
        fields.push(this.inputField(a, obj ? obj[a.key] : null, canEdit));
      }
    }
    for (const r of c.relationships) fields.push(this.linkField(r));
    if (IS_ADMIN) fields.push(this.rolesField());
    else if (obj && obj._roles.length) fields.push(this.rolesBadges(obj._roles));
    $('atlas-object-form').innerHTML = fields.join('') || '<p class="text-muted mb-0">This class has no fields yet.</p>';

    for (const r of c.relationships) this.initLinkSelect(r, obj ? obj[r.key] : null, canEdit);
    if (IS_ADMIN) this.initRolesSelect(obj ? obj._roles : [], canEdit);

    $('atlas-object-incoming').innerHTML = obj ? this.incomingHtml(obj._incoming || []) : '';
    $('atlas-object-meta').textContent = obj
      ? `Created by ${obj._created_by}, ${fmtStamp(obj._created_at)} · last changed by ${obj._updated_by}, `
        + `${fmtStamp(obj._updated_at)} · version ${obj._version}`
      : '';
    $('atlas-object-save').classList.toggle('d-none', !canEdit);
    $('atlas-object-delete').classList.toggle('d-none', isNew || !(obj._can && obj._can.delete));
    if (!isNew) history.replaceState(null, '', `#${c.key}/${obj._id}`);
  }

  inputField(a, value, canEdit) {
    const id = `f-${a.key}`;
    const required = a.required ? ' <span class="text-danger">*</span>' : '';
    const common = `id="${id}" data-key="${esc(a.key)}" data-type="${a.type}" ${canEdit ? '' : 'disabled'}`;
    let input;
    let wide = false;
    switch (a.type) {
      case 'text':
        wide = true;
        input = `<textarea class="form-control" rows="3" ${common}>${esc(value)}</textarea>`;
        break;
      case 'int':
        input = `<input type="number" step="1" class="form-control" value="${esc(value)}" ${common}>`;
        break;
      case 'float':
        input = `<input type="number" step="any" class="form-control" value="${esc(value)}" ${common}>`;
        break;
      case 'date':
        input = `<input type="date" class="form-control" value="${esc(value)}" ${common}>`;
        break;
      case 'datetime':
        input = `<input type="datetime-local" step="1" class="form-control" value="${esc(value)}" ${common}>`;
        break;
      case 'boolean':
        return `<div class="col-md-6"><div class="form-check form-switch mt-md-4 pt-md-2">
          <input class="form-check-input" type="checkbox" ${common} ${value ? 'checked' : ''}>
          <label class="form-check-label" for="${id}">${esc(a.label)}${required}</label></div></div>`;
      case 'choice': {
        const options = [...(a.options || [])];
        if (value != null && !options.includes(value)) options.push(value);   // keep an old value visible
        input = `<select class="form-select" ${common}><option value="">—</option>${options.map(o =>
          `<option value="${esc(o)}" ${o === value ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
        break;
      }
      default:
        input = `<input type="text" class="form-control" value="${esc(value)}" ${common}>`;
    }
    return `<div class="${wide ? 'col-12' : 'col-md-6'}">
      <label class="form-label" for="${id}">${esc(a.label)}${required}</label>${input}</div>`;
  }

  computedField(a, value) {
    const text = fmtValue(a.value_type, value);
    return `<div class="col-md-6"><span class="form-label d-block text-muted">${esc(a.label)}</span>
      <div class="form-control-plaintext py-1">${text ? esc(text) : '<span class="text-muted">—</span>'}</div></div>`;
  }

  linkField(r) {
    const required = r.required ? ' <span class="text-danger">*</span>' : '';
    return `<div class="col-12">
      <label class="form-label" for="f-${r.key}">${esc(r.label)}${required}
        <span class="text-muted small ms-1">${esc(r.target_label)}</span></label>
      <select id="f-${r.key}" ${r.cardinality === 'many' ? 'multiple' : ''} placeholder="Search ${esc(r.target_label)}…"></select>
    </div>`;
  }

  initLinkSelect(r, value, canEdit) {
    const current = value == null ? [] : (Array.isArray(value) ? value : [value]);
    const url = this.objectsUrl(r.target);
    const select = new TomSelect(`#f-${r.key}`, {
      valueField: '_id',
      labelField: '_title',
      searchField: ['_title'],
      options: current,
      items: current.map(o => o._id),
      maxItems: r.cardinality === 'one' ? 1 : null,
      plugins: r.cardinality === 'one' ? ['clear_button'] : ['remove_button'],
      preload: 'focus',
      loadThrottle: 250,
      dropdownParent: 'body',
      score: () => () => 1,              // the server has already searched
      load: (query, callback) => {
        const qs = new URLSearchParams({ fields: '_title', page_size: 50, sort: '_title' });
        if (query) qs.set('q', query);
        api(`${url}?${qs}`).then(result => callback(result.items)).catch(() => callback());
      },
      render: {
        option: (d, escape) => `<div>${escape(d._title)} <span class="text-muted small">#${escape(d._id)}</span></div>`,
        item: (d, escape) => `<div>${escape(d._title)}</div>`,
        no_results: () => '<div class="no-results">No matches</div>',
      },
    });
    if (!canEdit) select.disable();
    this.selects[r.key] = select;
  }

  rolesField() {
    const classRoles = (this.cls.roles || []).map(id => this.roleNames[id] || `#${id}`);
    const help = classRoles.length
      ? `${classRoles.join(', ')} can always work with ${this.cls.label}. Roles chosen here get access to this object as well.`
      : 'No roles: everyone can work with this object. With roles: only those roles, as their permissions allow.';
    return `<div class="col-12">
      <label class="form-label" for="f-_roles">Roles</label>
      <select id="f-_roles" multiple placeholder="${classRoles.length ? 'No extra roles' : 'Everyone'}"></select>
      <div class="form-text">${esc(help)}</div>
    </div>`;
  }

  initRolesSelect(values, canEdit) {
    const select = new TomSelect('#f-_roles', {
      options: this.roles.map(r => ({ value: String(r.id), text: r.rolename })),
      items: values.map(String),
      plugins: ['remove_button'],
      dropdownParent: 'body',
      maxOptions: null,
    });
    if (!canEdit) select.disable();
    this.selects._roles = select;
  }

  rolesBadges(roles) {
    return `<div class="col-12"><span class="form-label d-block text-muted">Roles</span>
      ${roles.map(id => `<span class="badge text-bg-light border me-1">${esc(this.roleNames[id] || `#${id}`)}</span>`).join('')}
    </div>`;
  }

  incomingHtml(groups) {
    if (!groups.length) return '';
    return '<hr><div class="small text-muted mb-2">Linked from</div>' + groups.map(g => `
      <div class="mb-2 small"><span class="me-2">${esc(g.label)}</span>${g.items.map(i =>
        `<a class="badge text-bg-light border text-decoration-none me-1" href="#${esc(g.class)}/${i._id}">${esc(i._title)}</a>`
      ).join('')}</div>`).join('');
  }

  collect() {
    const out = {};
    for (const el of $('atlas-object-form').querySelectorAll('[data-type]')) {
      out[el.dataset.key] = el.dataset.type === 'boolean' ? el.checked : (el.value === '' ? null : el.value);
    }
    for (const r of this.cls.relationships) {
      const select = this.selects[r.key];
      if (!select) continue;
      const value = select.getValue();
      out[r.key] = r.cardinality === 'one' ? (value ? Number(value) : null) : (value || []).map(Number);
    }
    if (this.selects._roles) out._roles = this.selects._roles.getValue().map(Number);
    if (this.editing) out._version = this.editing._version;
    return out;
  }

  async save() {
    this.clearError();
    const bad = [...$('atlas-object-form').querySelectorAll('input[data-type]')].find(el => el.validity.badInput);
    if (bad) {
      const attr = this.cls.attributes.find(a => a.key === bad.dataset.key);
      this.showError(`${attr ? attr.label : 'A field'} has a value that isn't valid.`);
      return;
    }
    $('atlas-object-save').disabled = true;
    try {
      if (this.editing) {
        await api(`${this.objectsUrl()}/${this.editing._id}`, { method: 'PATCH', body: this.collect() });
      } else {
        await api(this.objectsUrl(), { method: 'POST', body: this.collect() });
      }
      this.modal.hide();
      this.refreshGrid();
    } catch (e) {
      this.showError(e.message);
    } finally {
      $('atlas-object-save').disabled = false;
    }
  }

  async remove(id) {
    if (!confirm(`Delete ${this.cls.label} #${id}? This can't be undone.`)) return;
    try {
      await api(`${this.objectsUrl()}/${id}`, { method: 'DELETE' });
      if (this.editing && this.editing._id === id) this.modal.hide();
      this.refreshGrid();
    } catch (e) {
      if (this.editing) this.showError(e.message);
      else this.notice(e.message);
    }
  }

  showError(message) {
    $('atlas-object-error').textContent = message;
    $('atlas-object-error').classList.remove('d-none');
  }

  clearError() {
    $('atlas-object-error').classList.add('d-none');
  }
}

document.addEventListener('DOMContentLoaded', () => new AtlasBrowser());
