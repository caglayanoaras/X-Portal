// Atlas import: an Excel or CSV file into the open class. The server reads the file each
// time (nothing is kept between requests): first a check (dry run) that matches columns to
// fields and tries every row, then, when no row has a problem, the import itself.
import { esc, postForm } from './atlas-common.js';

const $ = id => document.getElementById(id);
const ACCEPT = ['.xlsx', '.xlsm', '.csv'];
const ROWS_SHOWN = 8;            // row numbers listed per problem before "and N more"

export class ImportDialog {
  constructor({ classesUrl, maxFileMb = 25, onImported = () => {} }) {
    Object.assign(this, { classesUrl, maxFileMb, onImported });
    this.modal = bootstrap.Modal.getOrCreateInstance($('atlas-import-modal'));
    this.token = 0;
    this.bind();
  }

  bind() {
    const drop = $('atlas-import-drop');
    $('atlas-import-choose').addEventListener('click', () => $('atlas-import-file').click());
    drop.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        $('atlas-import-file').click();
      }
    });
    $('atlas-import-file').addEventListener('change', e => {
      if (e.target.files[0]) this.pick(e.target.files[0]);
      e.target.value = '';
    });
    drop.addEventListener('dragover', e => {
      e.preventDefault();
      drop.classList.add('dragging');
    });
    drop.addEventListener('dragleave', e => { if (!drop.contains(e.relatedTarget)) drop.classList.remove('dragging'); });
    drop.addEventListener('drop', e => {
      e.preventDefault();
      drop.classList.remove('dragging');
      if (e.dataTransfer.files[0]) this.pick(e.dataTransfer.files[0]);
    });
    const review = $('atlas-import-review');
    review.addEventListener('change', e => {
      if (e.target.matches('[data-column]')) this.remap(e.target);
      if (e.target.matches('#atlas-import-sheet')) {
        this.sheet = e.target.value;
        this.mapping = null;                       // another sheet: match its columns afresh
        this.check();
      }
      if (e.target.matches('#atlas-import-match')) {
        this.match = e.target.value;
        this.check();
      }
    });
    review.addEventListener('click', e => {
      if (e.target.closest('[data-import-again]')) this.start();
    });
    $('atlas-import-apply').addEventListener('click', () => this.apply());
    $('atlas-import-modal').addEventListener('hidden.bs.modal', () => {
      this.token += 1;                             // answers still on their way are dropped
      this.file = null;
    });
  }

  open(cls) {
    this.cls = cls;
    $('atlas-import-title').textContent = `Import into ${cls.label}`;
    $('atlas-import-template').href = `${this.url()}/template.xlsx`;
    $('atlas-import-limit').textContent = `.xlsx, .xlsm or .csv, up to ${this.maxFileMb} MB`;
    this.start();
    this.modal.show();
  }

  url() {
    return `${this.classesUrl}/${encodeURIComponent(this.cls.key)}/import`;
  }

  // Back to choosing a file.
  start() {
    this.token += 1;
    Object.assign(this, { file: null, report: null, sheet: null, mapping: null, match: '', done: false });
    $('atlas-import-start').classList.remove('d-none');
    $('atlas-import-review').classList.add('d-none');
    $('atlas-import-review').innerHTML = '';
    this.error('');
    this.footer();
  }

  pick(file) {
    const ext = `.${(file.name.split('.').pop() || '').toLowerCase()}`;
    if (!ACCEPT.includes(ext)) {
      this.error(`${file.name} isn't an Excel workbook (.xlsx) or a CSV file.`);
      return;
    }
    if (file.size > this.maxFileMb * 1024 * 1024) {
      this.error(`${file.name} is larger than ${this.maxFileMb} MB.`);
      return;
    }
    Object.assign(this, { file, report: null, sheet: null, mapping: null, match: '', done: false });
    this.check();
  }

  form(dryRun) {
    const form = new FormData();
    form.append('file', this.file, this.file.name);
    if (this.sheet) form.append('sheet', this.sheet);
    if (this.mapping) form.append('mapping', JSON.stringify(this.mapping));
    if (this.match) form.append('match', this.match);
    form.append('dry_run', dryRun ? 'true' : 'false');
    return form;
  }

  // Send the file for a dry run: the columns, the counts and the problems come back.
  async check() {
    const token = ++this.token;
    this.error('');
    this.busy = 'Checking…';
    this.footer();
    try {
      const report = await postForm(this.url(), this.form(true));
      if (token !== this.token) return;
      this.report = report;
      this.sheet = report.sheet;
      this.mapping = report.columns.map(c => c.field);
      this.render();
    } catch (e) {
      if (token !== this.token) return;
      this.error(e.message);
      if (!this.report) {
        this.file = null;
        $('atlas-import-review').classList.add('d-none');
        $('atlas-import-start').classList.remove('d-none');
      }
    }
    this.busy = '';
    this.footer();
  }

  async apply() {
    const token = ++this.token;
    this.busy = 'Importing…';
    this.footer();
    try {
      const report = await postForm(this.url(), this.form(false));
      if (token !== this.token) return;
      this.report = report;
      this.done = report.imported;
      this.render();
      if (report.imported) this.onImported();
    } catch (e) {
      if (token === this.token) this.error(e.message);
    }
    this.busy = '';
    this.footer();
  }

  // A column now goes into another field; a field takes one column, so another column
  // that went into it is skipped from now on.
  remap(select) {
    const index = Number(select.dataset.column);
    const key = select.value;
    if (key) {
      this.mapping = this.mapping.map((k, i) => (i !== index && k === key ? '' : k));
    }
    this.mapping[index] = key;
    if (this.match && !this.mapping.includes(this.match)) this.match = '';
    this.check();
  }

  render() {
    const r = this.report;
    $('atlas-import-start').classList.add('d-none');
    const review = $('atlas-import-review');
    review.classList.remove('d-none');
    const fields = Object.entries(r.fields);
    const required = new Set([
      ...this.cls.attributes.filter(a => a.required).map(a => a.key),
      ...this.cls.relationships.filter(x => x.required).map(x => x.key),
    ]);
    const option = (key, label, selected) =>
      `<option value="${esc(key)}" ${selected ? 'selected' : ''}>${esc(label)}${required.has(key) ? ' *' : ''}</option>`;
    const rows = r.columns.map((c, i) => `
      <tr>
        <td class="text-nowrap"><span class="atlas-import-letter">${esc(c.letter)}</span>${esc(c.header)}</td>
        <td class="atlas-import-examples">${c.examples.length ? c.examples.map(esc).join('<span class="atlas-sep">·</span>') : '<span class="text-muted">empty</span>'}</td>
        <td>
          <select class="form-select form-select-sm" data-column="${i}" aria-label="Where column ${esc(c.letter)} goes" ${this.done ? 'disabled' : ''}>
            <option value="" ${c.field ? '' : 'selected'}>Skip</option>
            ${fields.map(([key, label]) => option(key, label, key === c.field)).join('')}
          </select>
          ${c.note ? `<div class="small text-muted mt-1">${esc(c.note)}</div>` : ''}
        </td>
      </tr>`).join('');
    const unmapped = [...required].filter(key => r.fields[key] && !this.mapping.includes(key)).map(key => r.fields[key]);
    const matchable = r.matchable.filter(m => this.mapping.includes(m.key));
    const sheets = r.sheets.length > 1
      ? `<label class="atlas-import-sheet">Sheet <select class="form-select form-select-sm" id="atlas-import-sheet" ${this.done ? 'disabled' : ''}>
          ${r.sheets.map(s => `<option ${s === r.sheet ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select></label>` : '';
    review.innerHTML = `
      <div class="atlas-import-file">
        <i class="fas ${/\.csv$/i.test(r.file) ? 'fa-file-csv' : 'fa-file-excel'} fa-lg" style="color:#15803d"></i>
        <strong class="text-truncate">${esc(r.file)}</strong>
        ${sheets}
        ${this.done ? '' : '<button type="button" class="btn btn-link btn-sm p-0 ms-auto" data-import-again>Choose another file</button>'}
      </div>
      <div class="small text-muted mb-2">Row ${r.header_row} names the columns. ${r.rows.toLocaleString()} row${r.rows === 1 ? '' : 's'} below it.</div>
      <div class="table-responsive">
        <table class="table table-sm atlas-import-map">
          <thead><tr><th>Column</th><th>In the file</th><th>Goes into</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${unmapped.length ? `<div class="small text-muted mb-2">Required, but no column goes into ${unmapped.length === 1 ? 'it' : 'them'}:
          ${unmapped.map(esc).join(', ')}. New objects need ${unmapped.length === 1 ? 'it' : 'them'}.</div>` : ''}
      ${matchable.length ? `<label class="atlas-import-matching">Rows without an ID
          <select class="form-select form-select-sm" id="atlas-import-match" ${this.done ? 'disabled' : ''}>
            <option value="">become new objects</option>
            ${matchable.map(m => `<option value="${esc(m.key)}" ${m.key === this.match ? 'selected' : ''}>change the object with the same ${esc(m.label)}, if there is one</option>`).join('')}
          </select></label>` : ''}
      <div class="atlas-import-result">${this.resultHtml(r)}</div>`;
  }

  resultHtml(r) {
    const counts = [[r.created, 'new'], [r.updated, 'changed'], [r.unchanged, 'unchanged']]
      .filter(([n]) => n).map(([n, word]) => `${n.toLocaleString()} ${word}`).join(', ');
    if (r.imported) {
      return `<div class="alert alert-success py-2 mb-0"><i class="fas fa-circle-check me-2"></i>Imported: ${counts}.</div>`;
    }
    if (!r.rows) return '<div class="text-muted">There are no rows under the header row.</div>';
    if (!r.failed) {
      const nothing = !r.created && !r.updated;
      return `<div class="atlas-import-ready"><i class="fas fa-check me-2"></i>${nothing
        ? 'Every row matches what Atlas holds already: nothing to import.'
        : `Every row is fine: ${counts}.`}</div>`;
    }
    const listed = r.errors.reduce((sum, e) => sum + e.count, 0);
    const problems = r.errors.map(e => {
      const shown = e.rows.slice(0, ROWS_SHOWN).join(', ');
      const more = e.count - Math.min(e.rows.length, ROWS_SHOWN);
      return `<li><span class="atlas-import-rows">Row${e.count === 1 ? '' : 's'} ${shown}${more ? ` and ${more.toLocaleString()} more` : ''}</span>
        <span>${esc(e.message)}</span></li>`;
    }).join('');
    return `
      <div class="atlas-import-problems">
        <div class="mb-2"><i class="fas fa-triangle-exclamation me-2"></i>${r.failed.toLocaleString()} of ${r.rows.toLocaleString()}
          row${r.rows === 1 ? '' : 's'} can't go in, so nothing is imported yet. Fix them in the file and choose it again,
          or change where the columns go.</div>
        <ul>${problems}</ul>
        ${r.failed > listed ? `<div class="small text-muted">${(r.failed - listed).toLocaleString()} more rows have other problems.</div>` : ''}
      </div>`;
  }

  footer() {
    const r = this.report;
    const ready = r && this.file && !r.failed && !r.imported && (r.created || r.updated);
    const apply = $('atlas-import-apply');
    apply.classList.toggle('d-none', !!this.done);
    apply.disabled = !!this.busy || !ready;
    apply.innerHTML = `<i class="fas fa-file-import me-1"></i>${this.busy === 'Importing…' ? 'Importing…' : 'Import'}`;
    $('atlas-import-close').textContent = this.done ? 'Done' : 'Close';
    $('atlas-import-status').textContent = this.busy || '';
  }

  error(message) {
    $('atlas-import-error').textContent = message;
    $('atlas-import-error').classList.toggle('d-none', !message);
  }
}
