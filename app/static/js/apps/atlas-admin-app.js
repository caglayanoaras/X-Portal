// Atlas class builder: classes, their attributes, relationships and roles.
// Everything goes through the Atlas API (PUT replaces a class definition).
import {
  COLORS, DEFAULT_COLOR, ICONS, api, classIcon, esc, fmtStamp, fold, folderPaths, iconName, renderTree, slugify,
} from './atlas-common.js';

const root = document.getElementById('atlas-admin');
const URLS = { classes: root.dataset.urlClasses, roles: root.dataset.urlRoles, browser: root.dataset.urlBrowser };
const $ = id => document.getElementById(id);

const TYPES = [
  ['string', 'Text'], ['text', 'Long text'], ['int', 'Integer'], ['float', 'Float'],
  ['boolean', 'Yes / no'], ['date', 'Date'], ['datetime', 'Date and time'],
  ['choice', 'Choice'], ['user', 'User'], ['list', 'List of numbers'], ['matrix', 'Matrix of numbers'],
  ['image', 'Image'], ['file', 'File (PDF / Office)'], ['lookthrough', 'Look-through'],
];
const UPLOADS = ['image', 'file'];
const SERIES = ['list', 'matrix'];
const SELF = '__self__';   // target option for "this class" while it has no key yet

const MOVE_BUTTONS = `
  <div class="btn-group btn-group-sm">
    <button type="button" class="btn btn-outline-secondary" data-move="up" title="Move up"><i class="fas fa-arrow-up"></i></button>
    <button type="button" class="btn btn-outline-secondary" data-move="down" title="Move down"><i class="fas fa-arrow-down"></i></button>
    <button type="button" class="btn btn-outline-danger" data-move="remove" title="Remove"><i class="fas fa-times"></i></button>
  </div>`;

class ClassBuilder {
  constructor() {
    this.classes = [];      // summaries, as GET /classes returns them
    this.defs = {};         // key -> full definition
    this.roles = [];
    this.key = null;        // the class being edited; null while creating one
    this.color = '';        // '' = the default grey
    this.icon = '';         // '' = the default icon
    this.snapshot = '';     // the form as loaded, to notice unsaved changes
    this.rolesSelect = null;
    this.onForce = null;
    this.forceModal = bootstrap.Modal.getOrCreateInstance($('admin-force-modal'));
    this.columnsModal = bootstrap.Modal.getOrCreateInstance($('admin-columns-modal'));
    this.columnsRow = null;   // the attribute row whose matrix columns the modal edits
    this.renderColors();
    this.bind();
    this.init();
  }

  bind() {
    $('admin-form').addEventListener('submit', e => e.preventDefault());
    $('admin-new').addEventListener('click', () => this.go(null));
    for (const list of [$('admin-tree'), $('in-rows')]) {
      list.addEventListener('click', e => {
        const link = e.target.closest('a[data-key]');
        if (!link) return;
        e.preventDefault();
        this.go(link.dataset.key);
      });
    }
    $('add-attr').addEventListener('click', () => { this.addAttrRow({}, true); this.changedRows(); });
    $('add-rel').addEventListener('click', () => { this.addRelRow({}, true); this.changedRows(); });
    $('admin-save').addEventListener('click', () => this.save());
    $('admin-delete').addEventListener('click', () => this.remove());
    $('admin-force-confirm').addEventListener('click', () => {
      this.forceModal.hide();
      if (this.onForce) this.onForce();
    });
    $('cls-label').addEventListener('input', () => { this.autoKey($('cls-label'), $('cls-key')); this.renderHead(); });
    $('cls-key').addEventListener('input', () => { $('cls-key').dataset.auto = '0'; this.renderHead(); });
    $('cls-path').addEventListener('input', () => this.renderHead());

    // color: a preset swatch, or the custom one (a native color input)
    $('cls-colors').addEventListener('click', e => {
      const swatch = e.target.closest('button[data-color]');
      if (swatch) this.setColor(swatch.dataset.color);
    });
    $('cls-colors').addEventListener('input', e => {
      if (e.target.matches('input[type=color]')) this.setColor(e.target.value.toLowerCase());
    });

    // icon: a dropdown with a search box and a grid of icons
    $('cls-icon-button').addEventListener('shown.bs.dropdown', () => {
      $('cls-icon-search').value = '';
      this.renderIcons();
      $('cls-icon-search').focus();
    });
    $('cls-icon-search').addEventListener('input', () => this.renderIcons());
    $('cls-icon-search').addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const first = $('cls-icon-grid').querySelector('[data-icon]');
      if (first) this.pickIcon(first.dataset.icon);
    });
    $('cls-icon-grid').addEventListener('click', e => {
      const choice = e.target.closest('[data-icon]');
      if (choice) this.pickIcon(choice.dataset.icon);
    });
    $('cls-icon-default').addEventListener('click', () => this.pickIcon(''));

    for (const body of [$('attr-rows'), $('rel-rows')]) {
      body.addEventListener('input', e => {
        const row = e.target.closest('tr');
        if (e.target.matches('.a-label, .r-label')) this.autoKey(e.target, row.querySelector('.a-key, .r-key'));
        if (e.target.matches('.a-key, .r-key')) e.target.dataset.auto = '0';
        if (e.target.matches('.a-label, .a-key, .r-label, .r-key')) this.refreshLookups();
      });
      body.addEventListener('change', e => {
        if (e.target.matches('.a-type')) {
          const row = e.target.closest('tr');
          this.updateAttrRow(row);
          if (e.target.value === 'matrix' && !this.rowColumns(row).length) this.editColumns(row);
        }
        if (e.target.matches('.a-type, .r-target')) this.refreshLookups();
      });
      body.addEventListener('click', e => {
        if (e.target.closest('.a-columns-edit')) {
          this.editColumns(e.target.closest('tr'));
          return;
        }
        const button = e.target.closest('button[data-move]');
        if (!button) return;
        const row = button.closest('tr');
        if (button.dataset.move === 'up' && row.previousElementSibling) row.previousElementSibling.before(row);
        if (button.dataset.move === 'down' && row.nextElementSibling) row.nextElementSibling.after(row);
        if (button.dataset.move === 'remove') row.remove();
        this.changedRows();
      });
    }
    window.addEventListener('beforeunload', e => {
      if (this.dirty()) { e.preventDefault(); e.returnValue = ''; }
    });

    // matrix columns: a small editor in a modal (label, key, order)
    $('columns-add').addEventListener('click', () => { this.addColumnRow({}, true); this.renumberColumns(); });
    $('columns-rows').addEventListener('input', e => {
      const row = e.target.closest('tr');
      if (e.target.matches('.c-label')) this.autoKey(e.target, row.querySelector('.c-key'));
      if (e.target.matches('.c-key')) e.target.dataset.auto = '0';
    });
    $('columns-rows').addEventListener('click', e => {
      const button = e.target.closest('button[data-move]');
      if (!button) return;
      const row = button.closest('tr');
      if (button.dataset.move === 'up' && row.previousElementSibling) row.previousElementSibling.before(row);
      if (button.dataset.move === 'down' && row.nextElementSibling) row.nextElementSibling.after(row);
      if (button.dataset.move === 'remove') row.remove();
      this.renumberColumns();
    });
    $('columns-done').addEventListener('click', () => this.saveColumns());
    $('admin-columns-modal').addEventListener('shown.bs.modal', () => {
      const first = $('columns-rows').querySelector('.c-label');
      if (first) first.focus();
    });
  }

  async init() {
    try {
      const [classes, roles] = await Promise.all([api(URLS.classes), api(URLS.roles)]);
      this.roles = roles;
      await this.load(classes);
    } catch (e) {
      $('admin-message').textContent = e.message;
      return;
    }
    this.rolesSelect = new TomSelect('#cls-roles', {
      options: this.roles.map(r => ({ value: String(r.id), text: r.rolename })),
      plugins: ['remove_button'],
      maxOptions: null,
      hidePlaceholder: true,    // "Everyone" only while no role is picked
    });
    const wanted = decodeURIComponent(location.hash.slice(1));
    this.open(this.defs[wanted] ? wanted : (this.classes[0] ? this.classes[0].key : null));
  }

  async load(classes) {
    this.classes = classes;
    const defs = await Promise.all(classes.map(c => api(`${URLS.classes}/${encodeURIComponent(c.key)}`)));
    this.defs = Object.fromEntries(defs.map(d => [d.key, d]));
    // every existing folder, parents included, so a new class can go next to existing ones
    $('admin-paths').innerHTML = folderPaths(classes).map(p => `<option value="${esc(p)}">`).join('');
  }

  async reload() {
    await this.load(await api(URLS.classes));
  }

  renderList() {
    renderTree($('admin-tree'), this.classes, this.key, c => `#${c.key}`);
  }

  // Switch to another class (or a new one), asking first if there are unsaved changes.
  go(key) {
    if (this.dirty() && !confirm('Discard your unsaved changes?')) return;
    this.open(key);
  }

  open(key) {
    this.key = key;
    const def = key ? this.defs[key] : {
      key: '', label: '', path: '', description: '', color: '', icon: '', roles: [], attributes: [], relationships: [],
    };
    history.replaceState(null, '', key ? `#${key}` : location.pathname);
    $('admin-message').classList.add('d-none');
    $('admin-editor').classList.remove('d-none');
    this.hideMessages();
    $('admin-open').classList.toggle('d-none', !key);
    $('admin-open').href = key ? `${URLS.browser}#${key}` : '#';
    $('admin-delete').classList.toggle('d-none', !key);

    $('cls-label').value = def.label;
    $('cls-key').value = def.key;
    $('cls-key').readOnly = !!key;
    $('cls-key').dataset.auto = key ? '0' : '1';
    $('cls-path').value = def.path || '';
    $('cls-description').value = def.description || '';
    this.rolesSelect.setValue((def.roles || []).map(String), true);
    this.setColor(def.color || '');
    this.setIcon(def.icon || '');

    $('attr-rows').innerHTML = '';
    $('rel-rows').innerHTML = '';
    def.relationships.forEach(r => this.addRelRow(r, false));
    def.attributes.forEach(a => this.addAttrRow(a, false));
    this.changedRows();
    this.renderIncoming();
    this.renderList();
    this.snapshot = JSON.stringify(this.collect());
  }

  // Relationships of other classes (or this one) that point here, as saved: what this class's
  // grid rows show as buttons and its objects list under "Linked from". Each is changed on its own class.
  renderIncoming() {
    const rows = [];
    for (const def of Object.values(this.defs)) {
      for (const r of def.relationships) {
        if (this.key && r.target === this.key) rows.push({ def, r });
      }
    }
    rows.sort((a, b) => fold(a.def.label).localeCompare(fold(b.def.label)) || fold(a.r.label).localeCompare(fold(b.r.label)));
    $('in-card').classList.toggle('d-none', !this.key);
    $('in-table').classList.toggle('d-none', !rows.length);
    $('in-empty').classList.toggle('d-none', rows.length > 0);
    $('in-count').textContent = rows.length ? String(rows.length) : '';
    $('in-rows').innerHTML = rows.map(({ def, r }) => `
      <tr>
        <td><a href="#${esc(def.key)}" data-key="${esc(def.key)}" class="atlas-in-class">${classIcon(def.icon, def.color)}<span>${esc(def.label)}</span></a></td>
        <td>${esc(r.label)}</td>
        <td>${r.cardinality === 'many' ? 'Many objects' : 'One object'}</td>
        <td>${esc(r.reverse_label || `${def.label} › ${r.label}`)}</td>
        <td class="col-flag">${r.show === false ? '<span class="text-muted">—</span>' : '<i class="fas fa-check text-success" aria-label="Shown"></i>'}</td>
      </tr>`).join('');
  }

  autoKey(labelInput, keyInput) {
    if (keyInput && !keyInput.readOnly && keyInput.dataset.auto === '1') keyInput.value = slugify(labelInput.value);
  }

  // The header: folder, icon and label, key and history.
  renderHead() {
    const def = this.key ? this.defs[this.key] : null;
    const path = $('cls-path').value.trim().split('/').map(s => s.trim()).filter(Boolean).join(' / ');
    const key = $('cls-key').value.trim();
    $('admin-kicker').textContent = def ? (path || 'Class') : 'New class';
    $('admin-title-icon').innerHTML = classIcon(this.icon, this.color);
    $('admin-title').textContent = $('cls-label').value.trim() || (def ? def.label : 'Untitled class');
    const parts = key ? [`<code>${esc(key)}</code>`] : [];
    if (def) {
      parts.push(esc(`Created by ${def.created_by}, ${fmtStamp(def.created_at)}`));
      parts.push(esc(`last changed by ${def.updated_by}, ${fmtStamp(def.updated_at)}`));
    }
    $('admin-sub').innerHTML = parts.join(' · ');
  }

  // -- color and icon -----------------------------------------------------------
  renderColors() {
    const swatches = COLORS.map(([value, name]) => `
      <button type="button" class="atlas-swatch" data-color="${value}" style="--c:${value || DEFAULT_COLOR}"
              title="${name}" aria-label="${name}"></button>`);
    swatches.push(`
      <label class="atlas-swatch atlas-swatch-custom" title="Another color">
        <i class="fas fa-plus" aria-hidden="true"></i>
        <input type="color" value="#21358b" aria-label="Another color">
      </label>`);
    $('cls-colors').innerHTML = swatches.join('');
  }

  setColor(value) {
    this.color = /^#[0-9a-f]{6}$/.test(value) ? value : '';
    const preset = COLORS.some(([v]) => v === this.color);
    for (const swatch of $('cls-colors').querySelectorAll('button[data-color]')) {
      const on = swatch.dataset.color === this.color;
      swatch.classList.toggle('selected', on);
      swatch.setAttribute('aria-pressed', String(on));
    }
    const custom = $('cls-colors').querySelector('.atlas-swatch-custom');
    custom.classList.toggle('selected', !preset);
    custom.classList.toggle('has-color', !preset);
    custom.style.setProperty('--c', preset ? '' : this.color);
    if (!preset) custom.querySelector('input').value = this.color;
    this.setIcon(this.icon);                   // the icon previews take the color
  }

  setIcon(icon) {
    this.icon = /^fa-[a-z0-9-]+$/.test(icon || '') ? icon : '';
    $('cls-icon-preview').innerHTML = classIcon(this.icon, this.color);
    $('cls-icon-name').textContent = this.icon ? iconName(this.icon) : 'Default';
    $('cls-icon-grid').style.setProperty('--c', this.color || DEFAULT_COLOR);
    this.renderHead();
  }

  pickIcon(icon) {
    this.setIcon(icon);
    bootstrap.Dropdown.getOrCreateInstance($('cls-icon-button')).hide();
    $('cls-icon-button').focus();
  }

  renderIcons() {
    const words = fold($('cls-icon-search').value.trim());
    const all = this.icon && !ICONS.includes(this.icon) ? [this.icon, ...ICONS] : ICONS;   // keep an API-set icon
    const shown = all.filter(icon => !words || iconName(icon).includes(words));
    $('cls-icon-grid').innerHTML = shown.map(icon => `
      <button type="button" class="atlas-icon-choice ${icon === this.icon ? 'selected' : ''}" data-icon="${esc(icon)}"
              title="${esc(iconName(icon))}" aria-label="${esc(iconName(icon))}">
        <i class="fas ${esc(icon)} fa-fw" aria-hidden="true"></i></button>`).join('');
    $('cls-icon-none').classList.toggle('d-none', shown.length > 0);
  }

  // -- rows ------------------------------------------------------------------
  addAttrRow(a, isNew) {
    const type = a.type || 'string';
    const row = document.createElement('tr');
    row.innerHTML = `
      <td><input class="form-control form-control-sm a-label" maxlength="100" value="${esc(a.label)}" placeholder="Label"></td>
      <td><input class="form-control form-control-sm font-monospace a-key" maxlength="40" value="${esc(a.key)}"
                 data-auto="${isNew ? '1' : '0'}" ${isNew ? '' : 'readonly'}></td>
      <td><select class="form-select form-select-sm a-type">
        ${TYPES.map(([value, text]) => `<option value="${value}" ${value === type ? 'selected' : ''}>${text}</option>`).join('')}
      </select></td>
      <td>
        <input class="form-control form-control-sm a-options" placeholder="Option A, Option B"
               value="${esc(type === 'choice' ? (a.options || []).join(', ') : '')}">
        <select class="form-select form-select-sm a-lookup" data-value="${esc(a.lookup || '')}"></select>
        <div class="a-columns">
          <button type="button" class="btn btn-sm btn-outline-secondary a-columns-edit text-nowrap"
                  title="The matrix's columns"><i class="fas fa-table-columns me-1"></i>Columns</button>
          <span class="small text-muted a-columns-summary"></span>
        </div>
        <span class="text-muted small a-none">—</span>
      </td>
      <td class="col-flag"><input type="checkbox" class="form-check-input a-required" ${a.required ? 'checked' : ''}></td>
      <td class="col-flag"><input type="checkbox" class="form-check-input a-unique" ${a.unique ? 'checked' : ''}></td>
      <td class="col-flag"><input type="checkbox" class="form-check-input a-title" ${a.title ? 'checked' : ''}></td>
      <td class="col-flag"><input type="checkbox" class="form-check-input a-show" ${a.show === false ? '' : 'checked'}></td>
      <td class="col-move">${MOVE_BUTTONS}</td>`;
    // a matrix's columns; saved ones keep their keys (the values are stored by them)
    this.setRowColumns(row, (type === 'matrix' ? a.columns || [] : []).map(c => ({ ...c, saved: !isNew })));
    $('attr-rows').appendChild(row);
    this.updateAttrRow(row);
    if (isNew) row.querySelector('.a-label').focus();
  }

  updateAttrRow(row) {
    const type = row.querySelector('.a-type').value;
    row.querySelector('.a-options').classList.toggle('d-none', type !== 'choice');
    row.querySelector('.a-lookup').classList.toggle('d-none', type !== 'lookthrough');
    row.querySelector('.a-columns').classList.toggle('d-none', type !== 'matrix');
    row.querySelector('.a-none').classList.toggle('d-none', ['choice', 'lookthrough', 'matrix'].includes(type));
    // look-throughs can't be required or unique; images, files, lists and matrices can't be
    // unique or part of the title
    const whole = UPLOADS.includes(type) || SERIES.includes(type);
    const off = { '.a-required': type === 'lookthrough', '.a-unique': type === 'lookthrough' || whole, '.a-title': whole };
    for (const [selector, disabled] of Object.entries(off)) {
      const flag = row.querySelector(selector);
      flag.disabled = disabled;
      if (disabled) flag.checked = false;
    }
  }

  addRelRow(r, isNew) {
    const row = document.createElement('tr');
    const cardinality = r.cardinality || 'one';
    row.innerHTML = `
      <td><input class="form-control form-control-sm r-label" maxlength="100" value="${esc(r.label)}" placeholder="Label"></td>
      <td><input class="form-control form-control-sm font-monospace r-key" maxlength="40" value="${esc(r.key)}"
                 data-auto="${isNew ? '1' : '0'}" ${isNew ? '' : 'readonly'}></td>
      <td><select class="form-select form-select-sm r-target" ${isNew ? '' : 'disabled'}>${this.targetOptions(r.target)}</select></td>
      <td><select class="form-select form-select-sm r-card">
        <option value="one" ${cardinality === 'one' ? 'selected' : ''}>One object</option>
        <option value="many" ${cardinality === 'many' ? 'selected' : ''}>Many objects</option>
      </select></td>
      <td><input class="form-control form-control-sm r-reverse" maxlength="100" value="${esc(r.reverse_label)}" placeholder="e.g. Retest of"></td>
      <td class="col-flag"><input type="checkbox" class="form-check-input r-required" ${r.required ? 'checked' : ''}></td>
      <td class="col-flag"><input type="checkbox" class="form-check-input r-show" ${r.show === false ? '' : 'checked'}></td>
      <td class="col-move">${MOVE_BUTTONS}</td>`;
    $('rel-rows').appendChild(row);
    if (isNew) row.querySelector('.r-label').focus();
  }

  targetOptions(selected) {
    const options = this.classes.map(c => ({
      value: c.key, text: c.key === this.key ? `${c.label} (this class)` : c.label,
    }));
    if (!this.key) options.unshift({ value: SELF, text: 'This class' });
    if (!selected) options.unshift({ value: '', text: 'Choose…' });
    return options.map(o => `<option value="${esc(o.value)}" ${o.value === selected ? 'selected' : ''}>${esc(o.text)}</option>`).join('');
  }

  currentKey() {
    return this.key || $('cls-key').value.trim();
  }

  targetOf(row) {
    const value = row.querySelector('.r-target').value;
    return value === SELF ? this.currentKey() : value;
  }

  // Rows were added, moved or removed: counts, empty notes, look-through choices.
  changedRows() {
    for (const [kind, rows] of [['attr', $('attr-rows')], ['rel', $('rel-rows')]]) {
      const n = rows.children.length;
      $(`${kind}-table`).classList.toggle('d-none', n === 0);
      $(`${kind}-empty`).classList.toggle('d-none', n > 0);
      $(`${kind}-count`).textContent = n ? String(n) : '';
    }
    this.refreshLookups();
  }

  // Look-through choices: relationship › attribute of its target class.
  refreshLookups() {
    const own = this.collectAttributes();
    const choices = [];
    for (const row of $('rel-rows').children) {
      const key = row.querySelector('.r-key').value.trim();
      const target = this.targetOf(row);
      if (!key || !target) continue;
      const label = row.querySelector('.r-label').value.trim() || key;
      const attrs = target === this.currentKey() ? own : ((this.defs[target] || {}).attributes || []);
      for (const a of attrs) {        // images, files, lists and matrices can't be looked through
        if (a.key && !UPLOADS.includes(a.type) && !SERIES.includes(a.type)) {
          choices.push({ value: `${key}.${a.key}`, text: `${label} › ${a.label || a.key}` });
        }
      }
    }
    for (const select of $('attr-rows').querySelectorAll('.a-lookup')) {
      const current = select.value || select.dataset.value || '';
      let html = '<option value="">Choose…</option>' + choices.map(c =>
        `<option value="${esc(c.value)}" ${c.value === current ? 'selected' : ''}>${esc(c.text)}</option>`).join('');
      if (current && !choices.some(c => c.value === current)) {
        html += `<option value="${esc(current)}" selected>${esc(current)} (not available)</option>`;
      }
      select.innerHTML = html;
      select.dataset.value = current;
    }
  }

  // -- reading the form ------------------------------------------------------
  collectAttributes() {
    return [...$('attr-rows').children].map(row => {
      const type = row.querySelector('.a-type').value;
      const attr = {
        key: row.querySelector('.a-key').value.trim(),
        label: row.querySelector('.a-label').value.trim(),
        type,
        required: row.querySelector('.a-required').checked,
        unique: row.querySelector('.a-unique').checked,
        title: row.querySelector('.a-title').checked,
        show: row.querySelector('.a-show').checked,
      };
      if (type === 'choice') {
        attr.options = row.querySelector('.a-options').value.split(',').map(s => s.trim()).filter(Boolean);
      }
      if (type === 'lookthrough') {
        const select = row.querySelector('.a-lookup');
        attr.lookup = select.value || select.dataset.value || '';
      }
      if (type === 'matrix') attr.columns = this.rowColumns(row).map(c => ({ key: c.key, label: c.label }));
      return attr;
    });
  }

  // -- matrix columns --------------------------------------------------------
  rowColumns(row) {
    try {
      return JSON.parse(row.dataset.columns || '[]');
    } catch (e) {
      return [];
    }
  }

  setRowColumns(row, columns) {
    row.dataset.columns = JSON.stringify(columns);
    const summary = row.querySelector('.a-columns-summary');
    const names = columns.map(c => c.label).join(' · ');
    summary.textContent = columns.length ? names : 'none yet';
    summary.title = names;
  }

  editColumns(row) {
    this.columnsRow = row;
    const label = row.querySelector('.a-label').value.trim() || 'the matrix';
    $('columns-title').textContent = `Columns of ${label}`;
    $('columns-error').classList.add('d-none');
    $('columns-rows').innerHTML = '';
    const columns = this.rowColumns(row);
    columns.forEach(c => this.addColumnRow(c, !c.saved));
    if (!columns.length) {
      this.addColumnRow({}, true);
      this.addColumnRow({}, true);
    }
    this.renumberColumns();
    this.columnsModal.show();
  }

  addColumnRow(c, isNew) {
    const tr = document.createElement('tr');
    tr.dataset.saved = c.saved ? '1' : '';
    tr.innerHTML = `
      <td class="text-muted small c-number"></td>
      <td><input class="form-control form-control-sm c-label" maxlength="100" value="${esc(c.label)}"
                 placeholder="e.g. Stress (MPa)"></td>
      <td><input class="form-control form-control-sm font-monospace c-key" maxlength="40" value="${esc(c.key)}"
                 data-auto="${isNew ? '1' : '0'}" ${isNew ? '' : 'readonly'}></td>
      <td class="col-move">${MOVE_BUTTONS}</td>`;
    $('columns-rows').appendChild(tr);
    if (isNew && c.label === undefined) tr.querySelector('.c-label').focus();
  }

  renumberColumns() {
    [...$('columns-rows').children].forEach((tr, n) => { tr.querySelector('.c-number').textContent = n + 1; });
    $('columns-empty').classList.toggle('d-none', $('columns-rows').children.length > 0);
  }

  saveColumns() {
    const columns = [...$('columns-rows').children].map(tr => ({
      key: tr.querySelector('.c-key').value.trim(),
      label: tr.querySelector('.c-label').value.trim(),
      saved: tr.dataset.saved === '1',
    })).filter(c => c.label || c.key);
    let problem = '';
    const keys = new Set();
    for (const c of columns) {
      if (!c.label) problem = 'Every column needs a label.';
      else if (!/^[a-z](?:_?[a-z0-9])*$/.test(c.key)) problem = `The key of '${c.label}' must use lower-case letters, digits and single underscores, and start with a letter.`;
      else if (keys.has(c.key)) problem = `Two columns have the key '${c.key}'.`;
      keys.add(c.key);
      if (problem) break;
    }
    if (!problem && !columns.length) problem = 'A matrix needs at least one column.';
    if (problem) {
      $('columns-error').textContent = problem;
      $('columns-error').classList.remove('d-none');
      return;
    }
    this.setRowColumns(this.columnsRow, columns);
    this.columnsModal.hide();
  }

  collect() {
    return {
      key: $('cls-key').value.trim(),
      label: $('cls-label').value.trim(),
      path: $('cls-path').value.trim(),
      description: $('cls-description').value.trim(),
      color: this.color,
      icon: this.icon,
      roles: (this.rolesSelect ? this.rolesSelect.getValue() : []).map(Number),
      attributes: this.collectAttributes(),
      relationships: [...$('rel-rows').children].map(row => ({
        key: row.querySelector('.r-key').value.trim(),
        label: row.querySelector('.r-label').value.trim(),
        target: this.targetOf(row),
        cardinality: row.querySelector('.r-card').value,
        reverse_label: row.querySelector('.r-reverse').value.trim(),
        required: row.querySelector('.r-required').checked,
        show: row.querySelector('.r-show').checked,
      })),
    };
  }

  dirty() {
    return !$('admin-editor').classList.contains('d-none') && JSON.stringify(this.collect()) !== this.snapshot;
  }

  // -- saving ------------------------------------------------------------------
  async save(force = false) {
    this.hideMessages();
    const isNew = !this.key;
    const url = isNew ? URLS.classes : `${URLS.classes}/${encodeURIComponent(this.key)}${force ? '?force=true' : ''}`;
    $('admin-save').disabled = true;
    try {
      const saved = await api(url, { method: isNew ? 'POST' : 'PUT', body: this.collect() });
      await this.reload();
      this.open(saved.key);
      this.showSaved(isNew ? `Created ${saved.label}.` : 'Saved.');
    } catch (e) {
      if (e.requiresForce) this.askForce(e, () => this.save(true));
      else this.showError(e.message);
    } finally {
      $('admin-save').disabled = false;
    }
  }

  async remove(force = false) {
    const def = this.defs[this.key];
    if (!def) return;
    if (!force && !confirm(`Delete the class “${def.label}”?`)) return;
    this.hideMessages();
    try {
      await api(`${URLS.classes}/${encodeURIComponent(this.key)}${force ? '?force=true' : ''}`, { method: 'DELETE' });
      await this.reload();
      this.open(this.classes[0] ? this.classes[0].key : null);
      this.showSaved(`Deleted ${def.label}.`);
    } catch (e) {
      if (e.requiresForce) this.askForce(e, () => this.remove(true));
      else this.showError(e.message);
    }
  }

  askForce(error, then) {
    const items = error.warnings.length ? error.warnings : [error.message];
    $('admin-force-list').innerHTML = items.map(w => `<li>${esc(w)}</li>`).join('');
    this.onForce = then;
    this.forceModal.show();
  }

  showError(message) {
    $('admin-error').textContent = message;
    $('admin-error').classList.remove('d-none');
    $('admin-error').scrollIntoView({ block: 'nearest' });
  }

  showSaved(message) {
    $('admin-saved').textContent = message;
    $('admin-saved').classList.remove('d-none');
    window.scrollTo({ top: 0 });     // the message is at the top of the page (smooth unless motion is reduced)
    clearTimeout(this.savedTimer);
    this.savedTimer = setTimeout(() => $('admin-saved').classList.add('d-none'), 3000);
  }

  hideMessages() {
    $('admin-error').classList.add('d-none');
    $('admin-saved').classList.add('d-none');
  }
}

document.addEventListener('DOMContentLoaded', () => new ClassBuilder());
