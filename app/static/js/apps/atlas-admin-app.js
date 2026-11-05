// Atlas class builder: classes, their attributes, relationships and roles.
// Everything goes through the Atlas API (PUT replaces a class definition).
import { api, esc, fmtStamp, renderTree, slugify } from './atlas-common.js';

const root = document.getElementById('atlas-admin');
const URLS = { classes: root.dataset.urlClasses, roles: root.dataset.urlRoles, browser: root.dataset.urlBrowser };
const $ = id => document.getElementById(id);

const TYPES = [
  ['string', 'Text'], ['text', 'Long text'], ['int', 'Whole number'], ['float', 'Number'],
  ['boolean', 'Yes / no'], ['date', 'Date'], ['datetime', 'Date and time'],
  ['choice', 'Choice'], ['lookthrough', 'Look-through'],
];
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
    this.snapshot = '';     // the form as loaded, to notice unsaved changes
    this.rolesSelect = null;
    this.onForce = null;
    this.forceModal = bootstrap.Modal.getOrCreateInstance($('admin-force-modal'));
    this.bind();
    this.init();
  }

  bind() {
    $('admin-new').addEventListener('click', () => this.go(null));
    $('admin-tree').addEventListener('click', e => {
      const link = e.target.closest('a[data-key]');
      if (!link) return;
      e.preventDefault();
      this.go(link.dataset.key);
    });
    $('add-attr').addEventListener('click', () => { this.addAttrRow({}, true); this.changedRows(); });
    $('add-rel').addEventListener('click', () => { this.addRelRow({}, true); this.changedRows(); });
    $('admin-save').addEventListener('click', () => this.save());
    $('admin-delete').addEventListener('click', () => this.remove());
    $('admin-force-confirm').addEventListener('click', () => {
      this.forceModal.hide();
      if (this.onForce) this.onForce();
    });
    $('cls-label').addEventListener('input', () => this.autoKey($('cls-label'), $('cls-key')));
    $('cls-key').addEventListener('input', () => { $('cls-key').dataset.auto = '0'; });

    for (const body of [$('attr-rows'), $('rel-rows')]) {
      body.addEventListener('input', e => {
        const row = e.target.closest('tr');
        if (e.target.matches('.a-label, .r-label')) this.autoKey(e.target, row.querySelector('.a-key, .r-key'));
        if (e.target.matches('.a-key, .r-key')) e.target.dataset.auto = '0';
        if (e.target.matches('.a-label, .a-key, .r-label, .r-key')) this.refreshLookups();
      });
      body.addEventListener('change', e => {
        if (e.target.matches('.a-type')) this.updateAttrRow(e.target.closest('tr'));
        if (e.target.matches('.a-type, .r-target')) this.refreshLookups();
      });
      body.addEventListener('click', e => {
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
    });
    const wanted = decodeURIComponent(location.hash.slice(1));
    this.open(this.defs[wanted] ? wanted : (this.classes[0] ? this.classes[0].key : null));
  }

  async load(classes) {
    this.classes = classes;
    const defs = await Promise.all(classes.map(c => api(`${URLS.classes}/${encodeURIComponent(c.key)}`)));
    this.defs = Object.fromEntries(defs.map(d => [d.key, d]));
    const paths = [...new Set(classes.map(c => c.path).filter(Boolean))].sort();
    $('admin-paths').innerHTML = paths.map(p => `<option value="${esc(p)}">`).join('');
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
      key: '', label: '', path: '', description: '', roles: [], attributes: [], relationships: [],
    };
    history.replaceState(null, '', key ? `#${key}` : location.pathname);
    $('admin-message').classList.add('d-none');
    $('admin-editor').classList.remove('d-none');
    this.hideMessages();
    $('admin-title').textContent = key ? def.label : 'New class';
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

    $('attr-rows').innerHTML = '';
    $('rel-rows').innerHTML = '';
    def.relationships.forEach(r => this.addRelRow(r, false));
    def.attributes.forEach(a => this.addAttrRow(a, false));
    this.changedRows();
    $('admin-meta').textContent = key
      ? `Created by ${def.created_by}, ${fmtStamp(def.created_at)} · last changed by ${def.updated_by}, ${fmtStamp(def.updated_at)}`
      : '';
    this.renderList();
    this.snapshot = JSON.stringify(this.collect());
  }

  autoKey(labelInput, keyInput) {
    if (keyInput && !keyInput.readOnly && keyInput.dataset.auto === '1') keyInput.value = slugify(labelInput.value);
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
        <span class="text-muted small a-none">—</span>
      </td>
      <td class="col-flag"><input type="checkbox" class="form-check-input a-required" ${a.required ? 'checked' : ''}></td>
      <td class="col-flag"><input type="checkbox" class="form-check-input a-unique" ${a.unique ? 'checked' : ''}></td>
      <td class="col-flag"><input type="checkbox" class="form-check-input a-title" ${a.title ? 'checked' : ''}></td>
      <td class="col-flag"><input type="checkbox" class="form-check-input a-show" ${a.show === false ? '' : 'checked'}></td>
      <td class="col-move">${MOVE_BUTTONS}</td>`;
    $('attr-rows').appendChild(row);
    this.updateAttrRow(row);
    if (isNew) row.querySelector('.a-label').focus();
  }

  updateAttrRow(row) {
    const type = row.querySelector('.a-type').value;
    row.querySelector('.a-options').classList.toggle('d-none', type !== 'choice');
    row.querySelector('.a-lookup').classList.toggle('d-none', type !== 'lookthrough');
    row.querySelector('.a-none').classList.toggle('d-none', type === 'choice' || type === 'lookthrough');
    for (const flag of row.querySelectorAll('.a-required, .a-unique')) {
      flag.disabled = type === 'lookthrough';
      if (flag.disabled) flag.checked = false;
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

  changedRows() {
    $('attr-empty').classList.toggle('d-none', $('attr-rows').children.length > 0);
    $('rel-empty').classList.toggle('d-none', $('rel-rows').children.length > 0);
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
      for (const a of attrs) {
        if (a.key) choices.push({ value: `${key}.${a.key}`, text: `${label} › ${a.label || a.key}` });
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
      return attr;
    });
  }

  collect() {
    return {
      key: $('cls-key').value.trim(),
      label: $('cls-label').value.trim(),
      path: $('cls-path').value.trim(),
      description: $('cls-description').value.trim(),
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
    clearTimeout(this.savedTimer);
    this.savedTimer = setTimeout(() => $('admin-saved').classList.add('d-none'), 3000);
  }

  hideMessages() {
    $('admin-error').classList.add('d-none');
    $('admin-saved').classList.add('d-none');
  }
}

document.addEventListener('DOMContentLoaded', () => new ClassBuilder());
