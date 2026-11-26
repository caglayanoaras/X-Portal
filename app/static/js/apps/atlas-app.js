// Atlas: browse a class's objects in a grid; view, add, edit and delete them.
// The grid pages, sorts and filters on the server (AG Grid infinite row model),
// and the form is built from the class definition. URLs: #class or #class/id.
import {
  ApiError, FILE_TYPES, api, classIcon, esc, fileIcon, fmtLocal, fmtSize, fmtStamp, fold, renderTree, upload,
} from './atlas-common.js';
import { ImportDialog } from './atlas-import.js';
import {
  SERIES_TYPES, ScatterPlot, copyText, fmtNumber, parseSeries, seriesColor, seriesPoints, seriesText,
} from './atlas-plot.js';

const root = document.getElementById('atlas-app');
const URLS = { classes: root.dataset.urlClasses, roles: root.dataset.urlRoles, users: root.dataset.urlUsers,
               admin: root.dataset.urlAdmin };
const IS_ADMIN = root.dataset.isAdmin === 'true';
const PAGE_SIZE = 100;
const MAX_FILE_MB = Number(root.dataset.maxFileMb) || 25;
const ACCEPT = { image: root.dataset.imageAccept || '', file: root.dataset.fileAccept || '' };
const SHOWN_IMAGES = /^image\/(png|jpeg|gif|webp|bmp)$/;     // what a browser previews before upload (not TIFF)
const $ = id => document.getElementById(id);

const TEXT_FILTER = { filterOptions: ['contains', 'equals', 'notEqual', 'startsWith', 'blank', 'notBlank'], maxNumConditions: 1 };
const NUMBER_FILTER = {
  filterOptions: ['equals', 'notEqual', 'greaterThan', 'greaterThanOrEqual', 'lessThan', 'lessThanOrEqual',
                  'inRange', 'blank', 'notBlank'],
  maxNumConditions: 1,
};
const DATE_FILTER = { filterOptions: ['equals', 'greaterThan', 'lessThan', 'inRange', 'blank', 'notBlank'], maxNumConditions: 1 };
const LINK_FILTER = { filterOptions: ['contains', 'blank', 'notBlank'], maxNumConditions: 1 };
const SERIES_FILTER = { filterOptions: ['blank', 'notBlank'], maxNumConditions: 1 };
const COMPARE_MAX = 50;          // objects one plot of the table overlays
const TABLE_ROWS = 1000;         // rows the plot's table view shows (Copy takes them all)
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

const LIST_PAGE = 50;

// Linked objects with a search box, LIST_PAGE at a time: from the row (what an object
// links to: items) or from the server (what links to it, maybe thousands: fetchPage).
class LinkList {
  constructor({ input, list, display, items = null, fetchPage = null, onTotal = () => {} }) {
    Object.assign(this, { input, list, display, local: items, fetchPage, onTotal });
    this.token = 0;
    let timer;
    this.onInput = () => {
      clearTimeout(timer);
      timer = setTimeout(() => this.reset(), this.local ? 0 : 250);
    };
    this.onClick = e => {
      if (!e.target.closest('[data-more]')) return;
      e.preventDefault();
      this.more();
    };
    input.addEventListener('input', this.onInput);
    list.addEventListener('click', this.onClick);
    this.reset();
  }

  destroy() {
    this.token += 1;                                  // answers still on their way are dropped
    this.input.removeEventListener('input', this.onInput);
    this.list.removeEventListener('click', this.onClick);
  }

  reset() {
    Object.assign(this, { query: this.input.value.trim(), items: [], total: null, page: 0, error: '' });
    this.more();
  }

  async more() {
    const token = ++this.token;
    if (this.local) {
      const words = fold(this.query).split(/\s+/).filter(Boolean);
      const matching = this.local.filter(i => words.every(w => fold(`${i._title} #${i._id}`).includes(w)));
      this.page += 1;
      Object.assign(this, { total: matching.length, items: matching.slice(0, this.page * LIST_PAGE) });
    } else {
      this.loading = true;
      this.render();
      try {
        const result = await this.fetchPage(this.query, this.page + 1);
        if (token !== this.token) return;              // a newer search has started
        this.page += 1;
        Object.assign(this, { items: this.items.concat(result.items), total: result.total, error: '' });
      } catch (e) {
        if (token !== this.token) return;
        this.error = e.message;
      }
      this.loading = false;
    }
    this.render();
  }

  render() {
    const d = this.display;
    let html = this.items.map(i => `
      <li class="list-group-item p-0">
        <a class="atlas-link-item" href="#${esc(d.cls)}/${esc(i._id)}">
          ${classIcon(d.icon, d.color)}
          <span class="flex-grow-1">${esc(i._title)}</span>
          <span class="text-muted small">${esc(d.classLabel)} #${esc(i._id)}</span>
        </a>
      </li>`).join('');
    if (this.error) html += `<li class="list-group-item text-danger small">${esc(this.error)}</li>`;
    else if (!this.items.length) {
      html += `<li class="list-group-item text-muted">${this.loading ? 'Loading…' : this.query ? 'No matches.' : 'Nothing linked.'}</li>`;
    }
    if (!this.error && this.total != null && this.items.length < this.total) {
      html += `<li class="list-group-item atlas-more">
        <span class="text-muted small">${this.items.length.toLocaleString()} of ${this.total.toLocaleString()}</span>
        <button type="button" class="btn btn-link btn-sm p-0" data-more ${this.loading ? 'disabled' : ''}>
          ${this.loading ? 'Loading…' : 'Show more'}</button></li>`;
    }
    this.list.innerHTML = html;
    if (!this.query && this.total != null) this.onTotal(this.total);
  }
}

class AtlasBrowser {
  constructor() {
    this.classes = [];
    this.roles = [];
    this.roleNames = {};
    this.users = [];          // portal users, for user attributes (which hold their ids)
    this.userNames = {};
    this.cls = null;          // the open class's definition
    this.grid = null;
    this.kinds = {};          // grid column -> how its filter maps to the API
    this.search = '';
    this.editing = null;      // the object in the form; null for a new one
    this.selects = {};        // tom-select instances in the form
    this.fileChanges = {};    // attribute key -> {file, url} to upload or {remove: true}, sent on Save
    this.canEdit = false;     // may the form change the object
    this.pendingRoute = false;
    this.formPlots = {};      // attribute key -> the live plot beside a list or matrix field
    this.plotView = null;     // what the plot window shows (see openPlot)
    this.plotAxes = {};       // attribute key -> {x, y} last chosen in the plot window
    this.modal = bootstrap.Modal.getOrCreateInstance($('atlas-object-modal'));
    this.importer = new ImportDialog({ classesUrl: URLS.classes, maxFileMb: MAX_FILE_MB,
                                       onImported: () => this.refreshGrid() });
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
    for (const item of document.querySelectorAll('[data-export]')) {
      item.addEventListener('click', () => this.exportTable(item.dataset.export));
    }
    $('atlas-import').addEventListener('click', () => this.importer.open(this.cls));
    $('atlas-plot').addEventListener('click', () => this.plotTable());
    this.bindPlotWindow();
    this.bindSeriesFields();
    // "Linked from" in the form: the first few, and the whole list (searchable) on request.
    $('atlas-object-incoming').addEventListener('click', e => {
      const button = e.target.closest('[data-show-all]');
      if (button) this.showAllIncoming(button);
    });
    $('atlas-object-save').addEventListener('click', () => this.save());
    $('atlas-object-delete').addEventListener('click', () => { if (this.editing) this.remove(this.editing._id); });
    // A linked object in the links list: close the list first, then open the object.
    $('linksList').addEventListener('click', e => {
      const link = e.target.closest('a.atlas-link-item');
      if (!link) return;
      e.preventDefault();
      const modal = $('showLinksModal');
      modal.addEventListener('hidden.bs.modal', () => { location.hash = link.getAttribute('href'); }, { once: true });
      bootstrap.Modal.getOrCreateInstance(modal).hide();
    });
    this.bindFileFields();
    $('atlas-file-modal').addEventListener('hidden.bs.modal', () => { $('atlas-file-body').innerHTML = ''; });
    $('atlas-object-modal').addEventListener('hidden.bs.modal', () => {
      for (const select of Object.values(this.selects)) select.destroy();
      this.selects = {};
      this.dropInlineLists();
      this.dropFileChanges();
      this.dropFormPlots();
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
      [this.classes, this.roles, this.users] = await Promise.all([api(URLS.classes), api(URLS.roles),
                                                                 api(URLS.users)]);
    } catch (e) {
      this.message(esc(e.message));
      return;
    }
    this.roleNames = Object.fromEntries(this.roles.map(r => [r.id, r.rolename]));
    this.userNames = Object.fromEntries(this.users.map(u => [u.id, u.name]));
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
    renderTree($('atlas-tree'), shown, this.cls && this.cls.key, c => `#${c.key}`, { searching: !!filter });
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
    $('atlas-class-icon').innerHTML = classIcon(this.cls.icon, this.cls.color);
    $('atlas-class-label').textContent = this.cls.label;
    $('atlas-class-description').textContent = this.cls.description || '';
    $('atlas-new').classList.toggle('d-none', !this.cls.permissions.create);
    $('atlas-import').classList.toggle('d-none', !IS_ADMIN);      // imports are for admins; others use the form
    $('atlas-plot').classList.toggle('d-none', !this.seriesAttributes().length);
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
    // Relationship columns start hidden (the Columns panel shows them); the row buttons list the links.
    for (const r of c.relationships) {
      this.kinds[r.key] = 'link';
      cols.push({ field: r.key, headerName: r.label, hide: true, filter: 'agTextColumnFilter',
                  filterParams: LINK_FILTER, valueFormatter: p => this.linkText(p.value) });
    }
    // Roles and when and by whom an object was made or changed start hidden too.
    if (IS_ADMIN) {
      cols.push({ field: '_roles', headerName: 'Roles', hide: true, sortable: false, filter: false,
                  valueFormatter: p => (p.value || []).map(id => this.roleNames[id] || `#${id}`).join(', ') });
    }
    for (const [field, headerName, hide] of [['_updated_at', 'Updated', true], ['_updated_by', 'Updated by', true],
                                             ['_created_at', 'Created', true], ['_created_by', 'Created by', true]]) {
      const stamp = field.endsWith('_at');
      this.kinds[field] = stamp ? 'timestamp' : 'text';
      cols.push({ field, headerName, hide, filter: stamp ? 'agDateColumnFilter' : 'agTextColumnFilter',
                  filterParams: stamp ? DATE_FILTER : TEXT_FILTER,
                  valueFormatter: stamp ? (p => fmtStamp(p.value)) : undefined });
    }
    const width = 118 + 54 * (this.linkRelationships().length + this.incomingLinks().length);
    cols.push({ colId: '_actions', headerName: '', sortable: false, filter: false, resizable: false,
                suppressHeaderMenuButton: true, suppressColumnsToolPanel: true, pinned: 'right',
                width, minWidth: width, maxWidth: width, flex: 0, cellRenderer: p => this.actionButtons(p.data) });
    return cols;
  }

  // Relationships that get a button in each row ("Show" in the class builder).
  linkRelationships() {
    return this.cls.relationships.filter(r => r.show);
  }

  // The reverse side: relationships of other classes (or this one) pointing here.
  incomingLinks() {
    return (this.cls.incoming || []).filter(r => r.show);
  }

  attributeColumn(a) {
    const type = a.type === 'lookthrough' ? a.value_type : a.type;
    if (FILE_TYPES.includes(type)) {             // filters: by file name, or whether there is one
      this.kinds[a.key] = 'text';
      return { field: a.key, headerName: a.label, filter: 'agTextColumnFilter', filterParams: LINK_FILTER,
               minWidth: type === 'image' ? 100 : 170, cellRenderer: p => this.fileCell(p.data, a) };
    }
    if (SERIES_TYPES.includes(type)) {           // the grid gets counts (brief); a click plots the values
      this.kinds[a.key] = 'text';
      return { field: a.key, headerName: a.label, filter: 'agTextColumnFilter', filterParams: SERIES_FILTER,
               minWidth: 130, cellRenderer: p => this.seriesCell(p.data, a) };
    }
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
    } else if (type === 'user') {                // ids: the cell shows names, the filter ticks people
      Object.assign(col, { valueFormatter: p => this.userText(p.value), filter: 'agSetColumnFilter',
                           filterParams: { values: this.users.map(u => String(u.id)),
                                           valueFormatter: p => this.userLabel(p.value) } });
      this.kinds[a.key] = 'set';
    } else {
      Object.assign(col, { filter: 'agTextColumnFilter', filterParams: TEXT_FILTER });
      if (type === 'text') col.minWidth = 220;
      this.kinds[a.key] = 'text';
    }
    return col;
  }

  // -- images and files -------------------------------------------------------
  // The URL of an object's upload. v (the object's version) gives a changed file a new URL.
  fileUrl(id, key, { view = 'original', download = false, v } = {}) {
    const qs = new URLSearchParams();
    if (view !== 'original') qs.set('view', view);
    if (download) qs.set('download', 'true');
    if (v != null) qs.set('v', v);
    const query = qs.toString();
    return `${this.objectsUrl()}/${id}/files/${encodeURIComponent(key)}${query ? `?${query}` : ''}`;
  }

  fileCell(data, a) {
    const f = data && data[a.key];
    if (!f) return '';
    if (a.type === 'image') {
      return `<button type="button" class="atlas-thumb" data-action="file" data-attr="${esc(a.key)}" title="${esc(f.name)}">
        <img src="${esc(this.fileUrl(data._id, a.key, { view: 'thumb', v: data._version }))}" alt="" loading="lazy"></button>`;
    }
    const [icon, color] = fileIcon(f.name);
    return `<button type="button" class="atlas-file-link" data-action="file" data-attr="${esc(a.key)}" title="${esc(f.name)}">
      <i class="fas ${icon} fa-fw" style="color:${color}"></i><span>${esc(f.name)}</span></button>`;
  }

  // Open an image (or a PDF) in the viewer; other files show a card. Download saves the original.
  showFile(data, a) {
    const f = data && data[a.key];
    if (!f) return;
    const at = opts => this.fileUrl(data._id, a.key, { ...opts, v: data._version });
    const isImage = a.type === 'image';
    const isPdf = f.type === 'application/pdf';
    const pdfShown = isPdf && navigator.pdfViewerEnabled !== false;   // a browser can have its PDF viewer off
    const [icon, color] = fileIcon(f.name);
    $('atlas-file-title').textContent = `${a.label} · ${data._title || `#${data._id}`}`;
    $('atlas-file-body').innerHTML = isImage
      ? `<img src="${esc(at({ view: 'preview' }))}" alt="${esc(f.name)}" class="atlas-file-image">`
      : pdfShown
        ? `<iframe src="${esc(at({}))}" class="atlas-file-pdf" title="${esc(f.name)}"></iframe>`
        : `<div class="py-4"><i class="fas ${icon} fa-4x" style="color:${color}"></i>
             <div class="mt-3">${esc(f.name)}</div></div>`;
    $('atlas-file-meta').textContent = [f.name, fmtSize(f.size), f.width ? `${f.width} × ${f.height}` : '']
      .filter(Boolean).join(' · ');
    $('atlas-file-open').classList.toggle('d-none', !(isImage || isPdf));
    $('atlas-file-open').href = at({ view: isImage ? 'preview' : 'original' });
    $('atlas-file-download').href = at({ download: true });
    bootstrap.Modal.getOrCreateInstance($('atlas-file-modal')).show();
  }

  // -- lists and matrices ---------------------------------------------------------
  seriesAttributes() {
    return this.cls ? this.cls.attributes.filter(a => SERIES_TYPES.includes(a.type)) : [];
  }

  // "12 values" or "120 rows": the grid has the count only (brief=true)
  seriesCount(a, n) {
    const noun = a.type === 'list' ? (n === 1 ? 'value' : 'values') : (n === 1 ? 'row' : 'rows');
    return `${n.toLocaleString()} ${noun}`;
  }

  seriesCell(data, a) {
    const v = data && data[a.key];
    if (!v) return '';
    return `<button type="button" class="atlas-series-link" data-action="series" data-attr="${esc(a.key)}"
      title="Plot ${esc(a.label)}"><i class="fas fa-chart-line fa-fw"></i><span>${this.seriesCount(a, v.count)}</span></button>`;
  }

  // The plot axes of a list or matrix: x is a column index or -1 (the position), y a column index.
  axesFor(a) {
    if (a.type === 'list') return { x: -1, y: 0 };
    const n = (a.columns || []).length;
    const saved = this.plotAxes[a.key];
    if (saved && saved.x < n && saved.y < n) return saved;
    return n > 1 ? { x: 0, y: 1 } : { x: -1, y: 0 };
  }

  axisLabel(a, index) {
    if (a.type === 'list') return index < 0 ? 'Position' : a.label;
    return index < 0 ? 'Row' : (a.columns[index] || {}).label || `Column ${index + 1}`;
  }

  // One object's list or matrix, from its grid row.
  async showSeries(data, a) {
    try {
      const qs = new URLSearchParams({ _id: data._id, fields: `_title,${a.key}` });
      const result = await api(`${this.objectsUrl()}?${qs}`);
      const item = result.items[0];
      if (!item) throw new Error(`${this.cls.label} #${data._id} is gone.`);
      this.openPlot({ title: `${a.label} · ${item._title}`, attrs: [a], attr: a,
                      objects: [{ _id: item._id, _title: item._title, value: item[a.key] }] });
    } catch (e) {
      this.notice(e.message);
    }
  }

  // The "Plot" button: a list or matrix of the objects in the table (its search, filters
  // and sort), one series each, the first COMPARE_MAX of them.
  async plotTable(attr) {
    const attrs = this.seriesAttributes();
    const a = attr || (this.plotView && this.plotView.compare && this.plotView.attr) || attrs[0];
    if (!a) return;
    const button = $('atlas-plot');
    button.disabled = true;
    try {
      const qs = this.tableQuery();
      qs.set('fields', `_title,${a.key}`);
      qs.set('page_size', COMPARE_MAX);
      qs.append(`${a.key}__isnull`, 'false');
      const result = await api(`${this.objectsUrl()}?${qs}`);
      this.openPlot({ compare: true, title: `${a.label} · ${this.cls.label}`, attrs, attr: a, total: result.total,
                      objects: result.items.map(i => ({ _id: i._id, _title: i._title, value: i[a.key] })) });
    } catch (e) {
      this.notice(e.message);
    } finally {
      button.disabled = false;
    }
  }

  bindPlotWindow() {
    this.plot = new ScatterPlot($('atlas-plot-chart'), { height: 420 });
    $('atlas-plot-attr').addEventListener('change', () => {
      const a = this.plotView.attrs.find(x => x.key === $('atlas-plot-attr').value);
      if (a) this.plotTable(a);
    });
    for (const id of ['atlas-plot-x', 'atlas-plot-y']) {
      $(id).addEventListener('change', () => {
        const a = this.plotView.attr;
        this.plotAxes[a.key] = { x: Number($('atlas-plot-x').value), y: Number($('atlas-plot-y').value) };
        this.drawPlot();
      });
    }
    for (const button of document.querySelectorAll('[data-plot-view]')) {
      button.addEventListener('click', () => this.plotMode(button.dataset.plotView));
    }
    $('atlas-plot-copy').addEventListener('click', async () => {
      const label = $('atlas-plot-copy').querySelector('span');
      try {
        await copyText(this.plotRows().map(row => row.join('\t')).join('\r\n'));
        label.textContent = 'Copied';
      } catch (e) {
        label.textContent = 'Copy failed';
      }
      setTimeout(() => { label.textContent = 'Copy data'; }, 1600);
    });
    const legend = $('atlas-plot-legend');
    legend.addEventListener('pointerover', e => {
      const item = e.target.closest('[data-series]');
      if (item) this.plot.highlight(Number(item.dataset.series));
    });
    legend.addEventListener('pointerleave', () => this.plot.highlight(null));
    legend.addEventListener('click', e => {         // a series' name opens its object
      const link = e.target.closest('a[href]');
      if (!link) return;
      e.preventDefault();
      const modal = $('atlas-plot-modal');
      modal.addEventListener('hidden.bs.modal', () => { location.hash = link.getAttribute('href'); }, { once: true });
      bootstrap.Modal.getOrCreateInstance(modal).hide();
    });
  }

  // view: {title, attrs (to choose from, when comparing), attr, objects: [{_id, _title, value}],
  //        compare (one series per object), total (objects in the table)}
  openPlot(view) {
    this.plotView = view;
    const a = view.attr;
    $('atlas-plot-title').textContent = view.title;
    const choose = view.compare && view.attrs.length > 1;
    $('atlas-plot-attr-field').classList.toggle('d-none', !choose);
    $('atlas-plot-attr').innerHTML = view.attrs.map(x =>
      `<option value="${esc(x.key)}" ${x.key === a.key ? 'selected' : ''}>${esc(x.label)}</option>`).join('');
    const isMatrix = a.type === 'matrix';
    for (const id of ['atlas-plot-x-field', 'atlas-plot-y-field']) $(id).classList.toggle('d-none', !isMatrix);
    if (isMatrix) {
      const axes = this.axesFor(a);
      const option = (value, label, selected) => `<option value="${value}" ${selected ? 'selected' : ''}>${esc(label)}</option>`;
      $('atlas-plot-x').innerHTML = option(-1, 'Row', axes.x === -1)
        + a.columns.map((c, i) => option(i, c.label, axes.x === i)).join('');
      $('atlas-plot-y').innerHTML = a.columns.map((c, i) => option(i, c.label, axes.y === i)).join('');
    }
    const modal = bootstrap.Modal.getOrCreateInstance($('atlas-plot-modal'));
    this.plotMode('plot');
    this.drawPlot();
    modal.show();
  }

  drawPlot() {
    const view = this.plotView;
    const a = view.attr;
    const axes = a.type === 'matrix'
      ? { x: Number($('atlas-plot-x').value), y: Number($('atlas-plot-y').value) } : { x: -1, y: 0 };
    const series = view.objects.map(o => ({ name: o._title, id: o._id, points: seriesPoints(a.type, o.value, axes.x, axes.y) }));
    this.plotAxesNow = axes;
    const xLabel = this.axisLabel(a, axes.x);
    this.plot.set({ series, xLabel, yLabel: this.axisLabel(a, axes.y), rowLabel: a.type === 'list' ? 'Position' : 'Row',
                    emptyText: `No ${a.type === 'list' ? 'values' : 'rows'} with both ${xLabel} and ${this.axisLabel(a, axes.y)}` });
    const points = series.reduce((sum, s) => sum + s.points.length, 0);
    // a legend for two or more series; one series is named by the title
    const legend = $('atlas-plot-legend');
    legend.classList.toggle('d-none', series.length < 2);
    legend.innerHTML = series.length < 2 ? '' : series.map((s, i) => `
      <li data-series="${i}"><span class="atlas-plot-dot" style="background:${seriesColor(i, series.length)}"></span>
        <a href="#${esc(this.cls.key)}/${esc(s.id)}">${esc(s.name)}</a>
        <span class="text-muted">${s.points.length.toLocaleString()}</span></li>`).join('');
    const parts = [view.compare ? `${series.length.toLocaleString()} object${series.length === 1 ? '' : 's'}` : '',
                   `${points.toLocaleString()} point${points === 1 ? '' : 's'}`];
    if (view.compare && view.total > series.length) {
      parts.push(`the first ${series.length} of the ${view.total.toLocaleString()} in the table, as it is sorted`);
    }
    $('atlas-plot-meta').textContent = parts.filter(Boolean).join(' · ');
    if (!$('atlas-plot-table').classList.contains('d-none')) this.renderPlotTable();
  }

  plotMode(mode) {
    for (const button of document.querySelectorAll('[data-plot-view]')) {
      const on = button.dataset.plotView === mode;
      button.classList.toggle('active', on);
      button.setAttribute('aria-pressed', String(on));
    }
    $('atlas-plot-stage').classList.toggle('d-none', mode !== 'plot');
    $('atlas-plot-table').classList.toggle('d-none', mode !== 'table');
    if (mode === 'table') this.renderPlotTable();
  }

  // The values as rows (a header first): one object's whole list or matrix, or, when
  // comparing, a row per point (object, x, y). Copy takes these; the table shows them.
  plotRows() {
    const view = this.plotView;
    const a = view.attr;
    if (!view.compare) {
      const value = view.objects[0].value || [];
      if (a.type === 'list') return [['Position', a.label], ...value.map((v, i) => [String(i + 1), fmtNumber(v)])];
      return [['Row', ...a.columns.map(c => c.label)], ...value.map((row, i) => [String(i + 1), ...row.map(fmtNumber)])];
    }
    const axes = this.plotAxesNow;
    const rows = [[this.cls.label, this.axisLabel(a, axes.x), this.axisLabel(a, axes.y)]];
    for (const o of view.objects) {
      for (const [x, y] of seriesPoints(a.type, o.value, axes.x, axes.y)) rows.push([o._title, fmtNumber(x), fmtNumber(y)]);
    }
    return rows;
  }

  renderPlotTable() {
    const rows = this.plotRows();
    const [head, ...body] = rows;
    const shown = body.slice(0, TABLE_ROWS);
    $('atlas-plot-table').innerHTML = `
      <table class="table table-sm atlas-plot-values">
        <thead><tr>${head.map((h, i) => `<th class="${i ? 'num' : ''}">${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${shown.map(row => `<tr>${row.map((v, i) => `<td class="${i ? 'num' : ''}">${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody>
      </table>
      ${body.length > shown.length
        ? `<p class="small text-muted mb-0">The first ${shown.length.toLocaleString()} of ${body.length.toLocaleString()} rows. Copy data takes them all.</p>` : ''}`;
  }

  // -- list and matrix fields in the form ----------------------------------------
  seriesField(a, value, canEdit) {
    const id = `f-${a.key}`;
    const isList = a.type === 'list';
    const columns = isList ? '' : (a.columns || []).map(c => esc(c.label)).join(' <span class="atlas-sep">⇥</span> ');
    const hint = isList
      ? 'A number per line. Paste a column from Excel, or separate them with semicolons.'
      : 'A row per line, values separated by tabs: paste the cells straight from Excel.';
    return this.fieldRow(a.label, `
      <div class="atlas-series">
        <textarea class="form-control form-control-sm atlas-series-text" id="${id}" rows="8" spellcheck="false" wrap="off"
          data-series="${esc(a.key)}" ${canEdit ? '' : 'readonly'}
          placeholder="${isList ? '210\n212,5\n208' : '0\t0\n0,1\t210\n0,2\t395'}">${esc(seriesText(a.type, value))}</textarea>
        <div class="atlas-series-plot" data-series-plot></div>
      </div>
      <div class="form-text" data-series-status></div>
      ${canEdit ? `<div class="form-text mt-0">${hint}</div>` : ''}`,
      { id, required: a.required, sub: columns, attrs: `data-series-field="${esc(a.key)}"` });
  }

  bindSeriesFields() {
    let timer;
    $('atlas-object-form').addEventListener('input', e => {
      const area = e.target.closest('[data-series]');
      if (!area) return;
      clearTimeout(timer);
      timer = setTimeout(() => this.updateSeriesField(area.dataset.series), 150);
    });
  }

  initSeriesFields() {
    for (const a of this.seriesAttributes()) {
      const box = $('atlas-object-form').querySelector(`[data-series-field="${CSS.escape(a.key)}"]`);
      if (!box) continue;
      this.formPlots[a.key] = new ScatterPlot(box.querySelector('[data-series-plot]'), { height: 212, compact: true });
      this.updateSeriesField(a.key);
    }
  }

  // Read a list or matrix field: its count or what is wrong, and its plot.
  seriesValue(a) {
    const area = $('atlas-object-form').querySelector(`[data-series="${CSS.escape(a.key)}"]`);
    return parseSeries(a.type, area ? area.value : '', (a.columns || []).length);
  }

  updateSeriesField(key) {
    const a = this.seriesAttributes().find(x => x.key === key);
    const box = a && $('atlas-object-form').querySelector(`[data-series-field="${CSS.escape(key)}"]`);
    if (!box) return;
    const parsed = this.seriesValue(a);
    const status = box.querySelector('[data-series-status]');
    status.classList.toggle('text-danger', !!parsed.error);
    status.textContent = parsed.error ? `${a.label} ${parsed.error}.` : parsed.count ? this.seriesCount(a, parsed.count) : '';
    const plot = this.formPlots[key];
    if (!plot) return;
    const axes = a.type === 'matrix' && (a.columns || []).length > 1 ? { x: 0, y: 1 } : { x: -1, y: 0 };
    plot.set({ series: [{ name: a.label, points: parsed.error ? [] : seriesPoints(a.type, parsed.value, axes.x, axes.y) }],
               xLabel: this.axisLabel(a, axes.x), yLabel: this.axisLabel(a, axes.y),
               rowLabel: a.type === 'list' ? 'Position' : 'Row',
               emptyText: parsed.error ? 'Fix the values to see the plot' : 'The plot shows here' });
  }

  dropFormPlots() {
    for (const plot of Object.values(this.formPlots)) plot.destroy();
    this.formPlots = {};
  }

  // A user attribute's value (an id, or ids through a 'many' look-through) as names.
  userText(value) {
    if (value == null) return '';
    return (Array.isArray(value) ? value : [value]).map(id => this.userNames[id] || `#${id}`).join(', ');
  }

  // A name with the username, where two people could share the name (filter lists).
  userLabel(id) {
    const u = this.users.find(x => String(x.id) === String(id));
    if (!u) return `#${id}`;
    return u.name === u.username ? u.name : `${u.name} (${u.username})`;
  }

  linkText(value) {
    if (value == null) return '';
    return (Array.isArray(value) ? value : [value]).map(v => (v && typeof v === 'object' ? v._title : `#${v}`)).join(', ');
  }

  actionButtons(data) {
    if (!data) return '';
    const can = data._can || {};
    const button = (action, key, label, icon, color, n) => `
      <button type="button" class="btn btn-outline-secondary atlas-link-btn" data-action="${action}"
        data-rel="${esc(key)}" title="${esc(label)} (${n})" ${n ? '' : 'disabled'}>
        ${classIcon(icon, color)}<span>${n}</span></button>`;
    const links = this.linkRelationships().map(r =>
      button('links', r.key, r.label, r.target_icon, r.target_color, this.linked(data[r.key]).length)).join('');
    const counts = data._incoming_counts || {};
    const incoming = this.incomingLinks().map(r => {
      const key = `${r.class}.${r.relationship}`;
      return button('incoming', key, r.label, r.icon, r.color, counts[key] || 0);
    }).join('');
    return `<div class="atlas-row-actions">
      <div class="btn-group btn-group-sm atlas-row-main">
        <button type="button" class="btn btn-outline-primary" data-action="open" title="${can.update ? 'Edit' : 'View'}">
          <i class="fas ${can.update ? 'fa-pen' : 'fa-eye'}"></i></button>
        ${can.delete ? `<button type="button" class="btn btn-outline-danger" data-action="delete" title="Delete">
          <i class="fas fa-trash"></i></button>` : ''}
      </div>
      ${links ? `<div class="btn-group btn-group-sm">${links}</div>` : ''}
      ${incoming ? `<div class="btn-group btn-group-sm" title="Linked from">${incoming}</div>` : ''}
    </div>`;
  }

  // A relationship value as a list: null, one object or a list (ids, or {_id, _title} once expanded).
  linked(value) {
    if (value == null) return [];
    return Array.isArray(value) ? value : [value];
  }

  onCell(p) {
    const button = p.event && p.event.target.closest('button[data-action]');
    if (!button || !p.data) return;
    if (button.dataset.action === 'open') this.go(p.data._id);
    if (button.dataset.action === 'delete') this.remove(p.data._id);
    if (button.dataset.action === 'file') this.showFile(p.data, this.cls.attributes.find(a => a.key === button.dataset.attr));
    if (button.dataset.action === 'series') this.showSeries(p.data, this.cls.attributes.find(a => a.key === button.dataset.attr));
    if (button.dataset.action === 'links') {
      const rel = this.cls.relationships.find(r => r.key === button.dataset.rel);
      if (rel) this.showLinks(rel, p.data[rel.key]);
    }
    if (button.dataset.action === 'incoming') {
      const rel = this.incomingLinks().find(r => `${r.class}.${r.relationship}` === button.dataset.rel);
      if (rel) this.showIncoming(rel, p.data._id);
    }
  }

  // -- links list (the portal's links modal, as Users & Permissions lists roles) --
  // What this object links to: the titles are already in the row.
  showLinks(rel, value) {
    const items = this.linked(value).map(v => (v && typeof v === 'object' ? v : { _id: v, _title: `#${v}` }));
    this.openLinks(rel.label, { cls: rel.target, classLabel: rel.target_label, icon: rel.target_icon,
                                color: rel.target_color }, { items });
  }

  // What links to this object: from the other class, a page at a time (only objects you can read).
  showIncoming(rel, id) {
    this.openLinks(rel.label, this.incomingDisplay(rel), { fetchPage: this.incomingPages(rel, id) });
  }

  incomingDisplay(rel) {
    return { cls: rel.class, classLabel: rel.class_label, icon: rel.icon, color: rel.color };
  }

  incomingPages(rel, id) {
    return (query, page) => {
      const qs = new URLSearchParams({ [rel.relationship]: id, fields: '_title', sort: '_title', page,
                                       page_size: LIST_PAGE });
      if (query) qs.set('q', query);
      return api(`${this.objectsUrl(rel.class)}?${qs}`);
    };
  }

  openLinks(label, display, source) {
    let search = document.getElementById('atlas-links-search');
    if (!search) {                        // the modal is shared with other pages, so the box is added here
      search = this.searchBox('atlas-links-search');
      $('linksList').before(search);
      $('showLinksModal').addEventListener('shown.bs.modal', () => search.focus());
    }
    search.value = '';
    if (this.linkList) this.linkList.destroy();
    const title = $('showLinksModal').querySelector('.modal-title');
    title.textContent = label;
    this.linkList = new LinkList({ input: search, list: $('linksList'), display, ...source,
                                   onTotal: total => { title.textContent = `${label} (${total.toLocaleString()})`; } });
    bootstrap.Modal.getOrCreateInstance($('showLinksModal')).show();
  }

  searchBox(id) {
    const box = document.createElement('input');
    Object.assign(box, { type: 'search', placeholder: 'Search', autocomplete: 'off',
                         className: 'form-control form-control-sm mb-2' });
    if (id) box.id = id;
    box.setAttribute('aria-label', 'Search the list');
    return box;
  }

  // -- export ----------------------------------------------------------------------
  // The grid as it stands: its search, filters, sort and column order. Relationship
  // columns go in whether shown or not ("title (#id)"); reverse links as counts.
  tableQuery() {
    const qs = new URLSearchParams();
    if (this.search) qs.set('q', this.search);
    const sort = this.grid.getColumnState().filter(s => s.sort)
      .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))
      .map(s => (s.sort === 'desc' ? '-' : '') + s.colId).join(',');
    if (sort) qs.set('sort', sort);
    this.addFilters(qs, this.grid.getFilterModel());
    return qs;
  }

  exportColumns() {
    const links = new Set(this.cls.relationships.map(r => r.key));
    return this.grid.getColumnState()
      .filter(s => s.colId !== '_actions' && (!s.hide || links.has(s.colId)))
      .map(s => s.colId);
  }

  async exportTable(format) {
    const button = $('atlas-export');
    const label = button.querySelector('span');
    const qs = this.tableQuery();
    qs.set('fields', this.exportColumns().join(','));
    if (this.incomingLinks().length) qs.set('incoming_counts', 'true');
    qs.set('_tz', Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC');
    button.disabled = true;
    label.textContent = 'Exporting…';
    $('atlas-grid-error').classList.add('d-none');
    try {
      const r = await fetch(`${this.objectsUrl()}.${format}?${qs}`);
      if (!r.ok) {
        let body = null;
        try { body = await r.json(); } catch (e) { /* not JSON */ }
        throw new ApiError(r.status, body);
      }
      const named = /filename\*=UTF-8''([^;]+)/i.exec(r.headers.get('content-disposition') || '');
      const url = URL.createObjectURL(await r.blob());
      const link = Object.assign(document.createElement('a'),
                                 { href: url, download: named ? decodeURIComponent(named[1]) : `${this.cls.label}.${format}` });
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (e) {
      this.notice(e.message);
    } finally {
      button.disabled = false;
      label.textContent = 'Export';
    }
  }

  go(id) {
    location.hash = `${this.cls.key}/${id}`;
  }

  datasource() {
    return {
      getRows: async params => {
        const size = params.endRow - params.startRow;
        const qs = new URLSearchParams({ page: Math.floor(params.startRow / size) + 1, page_size: size, expand: '*',
                                         brief: 'true' });   // lists and matrices as counts
        if (this.incomingLinks().length) qs.set('incoming_counts', 'true');
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
    this.dropFileChanges();
    this.renderForm(obj);
    this.modal.show();
  }

  renderForm(obj) {
    const c = this.cls;
    const isNew = !obj;
    const canEdit = isNew ? c.permissions.create : !!(obj._can && obj._can.update);
    this.canEdit = canEdit;
    this.clearError();
    $('atlas-object-title').textContent = isNew ? `New · ${c.label}` : `${c.label} · ${obj._title}`;

    const fields = [];
    for (const a of c.attributes) {
      if (a.type === 'lookthrough') {
        if (!isNew) fields.push(this.computedField(a, obj[a.key]));
      } else if (FILE_TYPES.includes(a.type)) {
        fields.push(`<div class="atlas-field" data-file-field="${esc(a.key)}"></div>`);
      } else if (SERIES_TYPES.includes(a.type)) {
        fields.push(this.seriesField(a, obj ? obj[a.key] : null, canEdit));
      } else if (a.type === 'user') {
        fields.push(this.userField(a));
      } else {
        fields.push(this.inputField(a, obj ? obj[a.key] : null, canEdit));
      }
    }
    if (c.relationships.length) fields.push(this.formSection('Links'));
    for (const r of c.relationships) fields.push(this.linkField(r));
    if (IS_ADMIN) fields.push(this.formSection('Access'), this.rolesField());
    else if (obj && obj._roles.length) fields.push(this.formSection('Access'), this.rolesBadges(obj._roles));
    $('atlas-object-form').innerHTML = fields.join('') || '<p class="text-muted mb-0">This class has no fields yet.</p>';
    $('atlas-object-form').classList.toggle('atlas-readonly', !canEdit);   // values read as text, not greyed boxes

    for (const a of this.uploadAttributes()) this.renderFileField(a);
    this.dropFormPlots();
    this.initSeriesFields();
    for (const a of this.userAttributes()) this.initUserSelect(a, obj ? obj[a.key] : null, canEdit);
    for (const r of c.relationships) this.initLinkSelect(r, obj ? obj[r.key] : null, canEdit);
    if (IS_ADMIN) this.initRolesSelect(obj ? obj._roles : [], canEdit);

    this.dropInlineLists();
    $('atlas-object-incoming').innerHTML = obj ? this.incomingHtml(obj._incoming || []) : '';
    $('atlas-object-meta').textContent = obj
      ? `Created by ${obj._created_by}, ${fmtStamp(obj._created_at)} · last changed by ${obj._updated_by}, `
        + `${fmtStamp(obj._updated_at)} · version ${obj._version}`
      : '';
    $('atlas-object-save').classList.toggle('d-none', !canEdit);
    $('atlas-object-delete').classList.toggle('d-none', isNew || !(obj._can && obj._can.delete));
    if (!isNew) history.replaceState(null, '', `#${c.key}/${obj._id}`);
  }

  // One row of the form: the label on the left (with a note under it), the value on the right.
  // sub and hint are HTML; attrs go on the row.
  fieldRow(label, value, { id = '', required = false, sub = '', hint = '', attrs = '' } = {}) {
    const mark = required ? ' <span class="text-danger">*</span>' : '';
    const note = sub ? `<span class="atlas-field-sub">${sub}</span>` : '';
    const name = id ? `<label class="atlas-field-label" for="${id}">${esc(label)}${mark}${note}</label>`
      : `<span class="atlas-field-label">${esc(label)}${mark}${note}</span>`;
    return `<div class="atlas-field" ${attrs}>${name}
      <div class="atlas-field-value">${value}${hint ? `<div class="form-text">${hint}</div>` : ''}</div></div>`;
  }

  formSection(title) {
    return `<div class="atlas-form-section">${esc(title)}</div>`;
  }

  inputField(a, value, canEdit) {
    const id = `f-${a.key}`;
    const common = `id="${id}" data-key="${esc(a.key)}" data-type="${a.type}" ${canEdit ? '' : 'disabled'}`;
    let input;
    switch (a.type) {
      case 'text':
        input = `<textarea class="form-control form-control-sm" rows="3" ${common}>${esc(value)}</textarea>`;
        break;
      case 'int':
        input = `<input type="number" step="1" class="form-control form-control-sm" value="${esc(value)}" ${common}>`;
        break;
      case 'float':
        input = `<input type="number" step="any" class="form-control form-control-sm" value="${esc(value)}" ${common}>`;
        break;
      case 'date':
        input = `<input type="date" class="form-control form-control-sm" value="${esc(value)}" ${common}>`;
        break;
      case 'datetime':
        input = `<input type="datetime-local" step="1" class="form-control form-control-sm" value="${esc(value)}" ${common}>`;
        break;
      case 'boolean':
        input = `<div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" role="switch" ${common} ${value ? 'checked' : ''}></div>`;
        break;
      case 'choice': {
        const options = [...(a.options || [])];
        if (value != null && !options.includes(value)) options.push(value);   // keep an old value visible
        input = `<select class="form-select form-select-sm" ${common}><option value="">—</option>${options.map(o =>
          `<option value="${esc(o)}" ${o === value ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
        break;
      }
      default:
        input = `<input type="text" class="form-control form-control-sm" value="${esc(value)}" ${common}>`;
    }
    return this.fieldRow(a.label, input, { id, required: a.required });
  }

  // A look-through: read from a linked object, so shown as text, with where it comes from.
  computedField(a, value) {
    const text = a.value_type === 'user' ? this.userText(value) : fmtValue(a.value_type, value);
    const rel = this.cls.relationships.find(r => r.key === String(a.lookup || '').split('.')[0]);
    return this.fieldRow(a.label,
      `<div class="atlas-field-text">${text ? esc(text) : '<span class="text-muted">—</span>'}</div>`,
      { sub: rel ? `from ${esc(rel.label)}` : '' });
  }

  userAttributes() {
    return this.cls.attributes.filter(a => a.type === 'user');
  }

  userField(a) {
    return this.fieldRow(a.label, `<select id="f-${a.key}" class="form-control-sm" placeholder="Choose a person…"></select>`,
                         { id: `f-${a.key}`, required: a.required });
  }

  // People with an active account, plus whoever the field names now (they may have left).
  initUserSelect(a, value, canEdit) {
    const options = this.users.filter(u => u.active || u.id === value)
      .map(u => ({ value: String(u.id), name: u.name, username: u.username, active: u.active }));
    if (value != null && !(value in this.userNames)) {
      options.push({ value: String(value), name: `#${value}`, username: '', active: false });
    }
    const inactive = d => (d.active ? '' : '<span class="text-muted small ms-2">inactive</span>');
    const select = new TomSelect(`#f-${a.key}`, {
      options,
      items: value == null ? [] : [String(value)],
      valueField: 'value',
      labelField: 'name',
      searchField: ['name', 'username'],
      maxItems: 1,
      maxOptions: null,
      plugins: ['clear_button'],
      dropdownParent: 'body',
      render: {
        option: (d, escape) => `<div>${escape(d.name)} <span class="text-muted small">${escape(d.username)}</span>${inactive(d)}</div>`,
        item: (d, escape) => `<div>${escape(d.name)}${inactive(d)}</div>`,
        no_results: () => '<div class="no-results">No matches</div>',
      },
    });
    if (!canEdit) select.disable();
    this.selects[a.key] = select;
  }

  linkField(r) {
    return this.fieldRow(r.label,
      `<select id="f-${r.key}" class="form-control-sm" ${r.cardinality === 'many' ? 'multiple' : ''}
               placeholder="Search ${esc(r.target_label)}…"></select>`,
      { id: `f-${r.key}`, required: r.required, sub: esc(r.target_label) });
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
    return this.fieldRow('Roles',
      `<select id="f-_roles" class="form-control-sm" multiple placeholder="${classRoles.length ? 'No extra roles' : 'Everyone'}"></select>`,
      { id: 'f-_roles', hint: esc(help) });
  }

  initRolesSelect(values, canEdit) {
    const select = new TomSelect('#f-_roles', {
      options: this.roles.map(r => ({ value: String(r.id), text: r.rolename })),
      items: values.map(String),
      plugins: ['remove_button'],
      dropdownParent: 'body',
      maxOptions: null,
      hidePlaceholder: true,    // "Everyone" / "No extra roles" only while no role is picked
    });
    if (!canEdit) select.disable();
    this.selects._roles = select;
  }

  rolesBadges(roles) {
    return this.fieldRow('Roles', `<div class="atlas-field-text">${roles.map(id =>
      `<span class="badge text-bg-light border me-1">${esc(this.roleNames[id] || `#${id}`)}</span>`).join('')}</div>`);
  }

  incomingHtml(groups) {
    if (!groups.length) return '';
    this.incomingGroups = groups;
    return this.formSection('Linked from') + groups.map((g, n) => {
      const count = g.count ?? g.items.length;
      const items = `<span class="atlas-incoming-items">${g.items.map(i =>
        `<a class="badge text-bg-light border text-decoration-none me-1" href="#${esc(g.class)}/${i._id}">${esc(i._title)}</a>`
      ).join('')}${count > g.items.length
        ? `<button type="button" class="btn btn-link btn-sm p-0 align-baseline" data-show-all="${n}">Show all ${count.toLocaleString()}</button>`
        : ''}</span>`;
      return this.fieldRow(g.label, `<div class="atlas-field-text">${items}</div>`, {
        sub: `${count.toLocaleString()} ${count === 1 ? 'object' : 'objects'}`, attrs: `data-group="${n}"` });
    }).join('');
  }

  // Swap a group's first few badges for the whole list: searchable, a page at a time.
  showAllIncoming(button) {
    const group = button.closest('[data-group]');
    const g = this.incomingGroups[Number(group.dataset.group)];
    const rel = (this.cls.incoming || []).find(r => r.class === g.class && r.relationship === g.relationship)
      || { class: g.class, class_label: g.class_label, relationship: g.relationship, icon: '', color: '' };
    const box = document.createElement('div');
    box.className = 'atlas-inline-links';
    const search = this.searchBox();
    const list = document.createElement('ul');
    list.className = 'list-group atlas-inline-list';
    box.append(search, list);
    group.querySelector('.atlas-incoming-items').replaceWith(box);
    this.inlineLists = this.inlineLists || [];
    this.inlineLists.push(new LinkList({ input: search, list, display: this.incomingDisplay(rel),
                                         fetchPage: this.incomingPages(rel, this.editing._id) }));
    search.focus();
  }

  dropInlineLists() {
    for (const list of this.inlineLists || []) list.destroy();
    this.inlineLists = [];
  }

  // -- image and file fields in the form ----------------------------------------
  uploadAttributes() {
    return this.cls ? this.cls.attributes.filter(a => FILE_TYPES.includes(a.type)) : [];
  }

  hasUpload(a) {
    const change = this.fileChanges[a.key];
    if (change) return !!change.file;
    return !!(this.editing && this.editing[a.key]);
  }

  // Choose, drop or paste a file; Remove and Undo. Nothing is sent until Save.
  bindFileFields() {
    const form = $('atlas-object-form');
    const attrOf = el => {
      const zone = el && el.closest('[data-drop]');
      return zone ? this.uploadAttributes().find(a => a.key === zone.dataset.drop) : null;
    };
    form.addEventListener('click', e => {
      const button = e.target.closest('[data-file-act]');
      const a = attrOf(button);
      if (!a) return;
      const act = button.dataset.fileAct;
      if (act === 'choose') button.closest('[data-drop]').querySelector('input[type=file]').click();
      if (act === 'remove') this.stageFile(a, this.editing && this.editing[a.key] ? { remove: true } : null);
      if (act === 'undo') this.stageFile(a, null);
    });
    form.addEventListener('change', e => {
      const a = e.target.matches('input[type=file]') ? attrOf(e.target) : null;
      if (a && e.target.files[0]) this.pickFile(a, e.target.files[0]);
    });
    form.addEventListener('dragover', e => {
      const zone = e.target.closest('[data-drop]');
      if (!zone || !this.canEdit) return;
      e.preventDefault();
      zone.classList.add('dragging');
    });
    form.addEventListener('dragleave', e => {
      const zone = e.target.closest('[data-drop]');
      if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove('dragging');
    });
    form.addEventListener('drop', e => {
      const a = attrOf(e.target);
      if (!a || !this.canEdit) return;
      e.preventDefault();
      e.target.closest('[data-drop]').classList.remove('dragging');
      if (e.dataTransfer.files[0]) this.pickFile(a, e.dataTransfer.files[0]);
    });
    // Ctrl+V with an image on the clipboard: into the image field that has focus, or the only one.
    $('atlas-object-modal').addEventListener('paste', e => {
      if (!this.canEdit || !e.clipboardData) return;
      const file = [...e.clipboardData.files].find(f => f.type.startsWith('image/'));
      const focus = document.activeElement;
      if (!file || (focus && focus.matches('input:not([type=file]), textarea, select'))) return;
      const images = this.uploadAttributes().filter(a => a.type === 'image');
      const focused = attrOf(focus);
      const a = focused && focused.type === 'image' ? focused : (images.length === 1 ? images[0] : null);
      if (!a) return;
      e.preventDefault();
      this.pickFile(a, file);
    });
  }

  pickFile(a, file) {
    this.clearError();
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      this.showError(`${file.name} is larger than ${MAX_FILE_MB} MB.`);
      return;
    }
    const ext = `.${(file.name.split('.').pop() || '').toLowerCase()}`;
    if (!ACCEPT[a.type].split(',').includes(ext)) {
      this.showError(a.type === 'image'
        ? `${a.label}: ${file.name} isn't a PNG, JPEG, GIF, WebP, TIFF or BMP image.`
        : `${a.label}: ${file.name} isn't a PDF, Word, Excel, PowerPoint, OpenDocument or CSV file.`);
      return;
    }
    this.stageFile(a, { file, url: a.type === 'image' && SHOWN_IMAGES.test(file.type) ? URL.createObjectURL(file) : null });
  }

  stageFile(a, change) {
    const old = this.fileChanges[a.key];
    if (old && old.url) URL.revokeObjectURL(old.url);
    if (change) this.fileChanges[a.key] = change;
    else delete this.fileChanges[a.key];
    this.renderFileField(a);
  }

  dropFileChanges() {
    for (const change of Object.values(this.fileChanges)) if (change.url) URL.revokeObjectURL(change.url);
    this.fileChanges = {};
  }

  renderFileField(a) {
    const box = $('atlas-object-form').querySelector(`[data-file-field="${CSS.escape(a.key)}"]`);
    if (!box) return;
    const obj = this.editing;
    const current = obj ? obj[a.key] : null;
    const change = this.fileChanges[a.key];
    const isImage = a.type === 'image';
    const glyph = name => {
      if (isImage || !name) return `<i class="fas ${isImage ? 'fa-image' : 'fa-file'} fa-2x text-muted"></i>`;
      const [icon, color] = fileIcon(name);
      return `<i class="fas ${icon} fa-2x" style="color:${color}"></i>`;
    };
    let preview;
    let info;
    if (change && change.file) {
      preview = change.url ? `<img src="${change.url}" alt="">` : glyph(change.file.name);
      info = `<div class="text-truncate">${esc(change.file.name)}</div>
        <div class="small text-muted">${fmtSize(change.file.size)} · uploaded when you save</div>`;
    } else if (change && change.remove) {
      preview = glyph('');
      info = `<div class="text-truncate text-decoration-line-through">${esc(current ? current.name : '')}</div>
        <div class="small text-muted">removed when you save</div>`;
    } else if (current) {
      const at = opts => this.fileUrl(obj._id, a.key, { ...opts, v: obj._version });
      preview = isImage ? `<img src="${esc(at({ view: 'thumb' }))}" alt="">` : glyph(current.name);
      const open = isImage || current.type === 'application/pdf'
        ? `<a href="${esc(at({ view: isImage ? 'preview' : 'original' }))}" target="_blank" rel="noopener">Open</a> · ` : '';
      info = `<div class="text-truncate">${esc(current.name)}</div>
        <div class="small text-muted">${fmtSize(current.size)}${current.width ? ` · ${current.width} × ${current.height}` : ''}
          · ${open}<a href="${esc(at({ download: true }))}">Download</a></div>`;
    } else {
      preview = glyph('');
      info = `<div class="text-muted">${isImage ? 'No image' : 'No file'}</div>`;
    }
    let actions = '';
    if (this.canEdit) {
      const has = (change && change.file) || (current && !(change && change.remove));
      actions = `<button type="button" class="btn btn-sm btn-outline-secondary" data-file-act="choose">
          <i class="fas fa-upload me-1"></i>${has ? 'Replace' : 'Choose'}</button>
        ${has ? '<button type="button" class="btn btn-sm btn-outline-danger" data-file-act="remove">Remove</button>' : ''}
        ${change ? '<button type="button" class="btn btn-sm btn-link px-1" data-file-act="undo">Undo</button>' : ''}`;
    }
    const kinds = isImage ? 'PNG, JPEG, GIF, WebP, TIFF or BMP' : 'PDF, Word, Excel, PowerPoint, OpenDocument or CSV';
    const required = a.required ? ' <span class="text-danger">*</span>' : '';
    box.innerHTML = `
      <span class="atlas-field-label">${esc(a.label)}${required}</span>
      <div class="atlas-field-value">
        <div class="atlas-drop" data-drop="${esc(a.key)}" tabindex="${this.canEdit ? 0 : -1}">
          <div class="atlas-drop-preview">${preview}</div>
          <div class="atlas-drop-info">${info}</div>
          <div class="atlas-drop-actions">${actions}</div>
          <input type="file" class="d-none" accept="${esc(ACCEPT[a.type])}">
        </div>
        ${this.canEdit ? `<div class="form-text">${kinds}, up to ${MAX_FILE_MB} MB. Drop a file here${isImage ? ' or paste one with Ctrl+V' : ''}.</div>` : ''}
      </div>`;
  }

  collect() {
    const out = {};
    for (const el of $('atlas-object-form').querySelectorAll('[data-type]')) {
      out[el.dataset.key] = el.dataset.type === 'boolean' ? el.checked : (el.value === '' ? null : el.value);
    }
    for (const a of this.seriesAttributes()) {
      if (this.formPlots[a.key]) out[a.key] = this.seriesValue(a).value;
    }
    for (const a of this.userAttributes()) {
      const select = this.selects[a.key];
      if (select) out[a.key] = select.getValue() ? Number(select.getValue()) : null;
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

  // Uploads are sent separately from the other fields. Editing: uploads first (a required
  // image can come in the same save), then the rest. New: the object first, then its uploads.
  async save() {
    this.clearError();
    const bad = [...$('atlas-object-form').querySelectorAll('input[data-type]')].find(el => el.validity.badInput);
    if (bad) {
      const attr = this.cls.attributes.find(a => a.key === bad.dataset.key);
      this.showError(`${attr ? attr.label : 'A field'} has a value that isn't valid.`);
      return;
    }
    const missing = this.uploadAttributes().filter(a => a.required && !this.hasUpload(a));
    if (missing.length) {
      this.showError(`Required: ${missing.map(a => a.label).join(', ')}.`);
      return;
    }
    for (const a of this.seriesAttributes()) {
      const parsed = this.formPlots[a.key] ? this.seriesValue(a) : {};
      if (parsed.error) {
        this.showError(`${a.label} ${parsed.error}.`);
        return;
      }
    }
    $('atlas-object-save').disabled = true;
    const wasNew = !this.editing;
    try {
      if (wasNew) {
        this.editing = await api(this.objectsUrl(), { method: 'POST', body: this.collect() });
        await this.sendUploads();
      } else {
        await this.sendUploads();
        this.editing = await api(`${this.objectsUrl()}/${this.editing._id}`, { method: 'PATCH', body: this.collect() });
      }
      this.modal.hide();
      this.refreshGrid();
    } catch (e) {
      this.showError(e.message);
      if (this.editing && this.editing._version != null) this.savedInPart(wasNew);
    } finally {
      $('atlas-object-save').disabled = false;
    }
  }

  // Send the staged uploads and removals, keeping this.editing at the latest version.
  async sendUploads() {
    for (const a of this.uploadAttributes()) {
      const change = this.fileChanges[a.key];
      if (!change) continue;
      const url = `${this.objectsUrl()}/${this.editing._id}/files/${encodeURIComponent(a.key)}`;
      this.editing = change.file
        ? await upload(url, change.file, this.editing._version)
        : await api(`${url}?_version=${this.editing._version}`, { method: 'DELETE' });
      this.stageFile(a, null);
    }
  }

  // Part of a save went through (e.g. the object was created but an upload failed):
  // the form now shows that object, and Save sends what is left.
  savedInPart(wasNew) {
    if (wasNew) {
      $('atlas-object-title').textContent = `${this.cls.label} · ${this.editing._title || `#${this.editing._id}`}`;
      history.replaceState(null, '', `#${this.cls.key}/${this.editing._id}`);
    }
    for (const a of this.uploadAttributes()) this.renderFileField(a);
    this.refreshGrid();
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
