// Lists and matrices: reading them from text, writing them as text, and scatter plots.
// A list is numbers ([210, 212.5]); a matrix is rows holding a number (or null) per
// column ([[0, 0], [0.1, 210]]). A list plots by position, a matrix one column against
// another. The text rules match the server's (app/atlas/values.py).

export const SERIES_TYPES = ['list', 'matrix'];

// The browser's decimal mark. Values are shown and copied with it, so a copy pastes into
// a spreadsheet as numbers; both marks are read back ("0,5" and "0.5").
const DECIMAL = (() => {
  try {
    const part = new Intl.NumberFormat().formatToParts(1.5).find(p => p.type === 'decimal');
    return part && part.value === ',' ? ',' : '.';
  } catch (e) {
    return '.';
  }
})();

// A number for an editor or a copy: exact, the browser's decimal mark, no thousands separator.
export function fmtNumber(n) {
  return n == null ? '' : String(n).replace('.', DECIMAL);
}

const CELL_SEPARATORS = /\t|;|,\s+/;       // between values on a line ("0,5" is one number)
const ROW_SEPARATORS = /\s*(?:\r?\n|\r|\|)\s*/;
const MAX_ROWS = 100000;

function toNumber(text) {
  let t = String(text).replace(/\s/g, '');
  if (t.includes(',') && !t.includes('.')) t = t.replaceAll(',', '.');
  return /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(t) ? Number(t) : NaN;
}

function splitLine(line) {
  return (CELL_SEPARATORS.test(line) ? line.split(CELL_SEPARATORS) : line.trim().split(/\s+/)).map(s => s.trim());
}

function cellNumber(cell, where, blankOk) {
  if (cell == null || (typeof cell === 'string' && !cell.trim())) {
    if (blankOk) return null;
    throw new Error(`must be numbers (${where} is empty)`);
  }
  const n = typeof cell === 'number' ? cell : (typeof cell === 'string' ? toNumber(cell) : NaN);
  if (!Number.isFinite(n)) throw new Error(`must be numbers (${where} is '${String(cell).trim().slice(0, 40)}')`);
  return n;
}

// Text (typed, or pasted from Excel: a line per row, tabs between values) or JSON ->
// {value, count} or {error}. value is null when there is nothing.
export function parseSeries(type, text, width = 0) {
  const t = String(text || '').trim();
  if (!t) return { value: null, count: 0 };
  let raw;
  if (t.startsWith('[')) {
    try {
      raw = JSON.parse(t);
    } catch (e) {
      return { error: "isn't valid JSON (write [1, 2] or 1; 2)" };
    }
    if (!Array.isArray(raw)) raw = [raw];
  } else if (type === 'list') {
    raw = t.split(/\r?\n|\r/).flatMap(splitLine).filter(Boolean);
  } else {
    raw = t.split(ROW_SEPARATORS).filter(r => r.trim()).map(splitLine);
  }
  try {
    if (type === 'list') {
      if (raw.length > MAX_ROWS) throw new Error(`has more than ${MAX_ROWS.toLocaleString()} values`);
      const value = raw.map((item, n) => cellNumber(Array.isArray(item) && item.length === 1 ? item[0] : item,
                                                    `value ${n + 1}`, false));
      return { value: value.length ? value : null, count: value.length };
    }
    if (raw.length > MAX_ROWS) throw new Error(`has more than ${MAX_ROWS.toLocaleString()} rows`);
    const rows = [];
    raw.forEach((row, n) => {
      if (!Array.isArray(row) && width !== 1) throw new Error(`row ${n + 1} must be a list of ${width} values`);
      const cells = Array.isArray(row) ? [...row] : [row];
      while (cells.length > width && (cells[cells.length - 1] == null || String(cells[cells.length - 1]).trim() === '')) {
        cells.pop();
      }
      if (cells.length > width) {
        throw new Error(`row ${n + 1} has ${cells.length} values but there ${width === 1 ? 'is 1 column' : `are ${width} columns`}`);
      }
      const numbers = cells.map(c => cellNumber(c, `row ${n + 1}`, true));
      if (numbers.some(v => v != null)) rows.push([...numbers, ...Array(width - numbers.length).fill(null)]);
    });
    return { value: rows.length ? rows : null, count: rows.length };
  } catch (e) {
    return { error: e.message };
  }
}

// The text an editor shows: a value per line (list), or a line per row with tabs between values.
export function seriesText(type, value) {
  if (!Array.isArray(value)) return '';
  if (type === 'list') return value.map(fmtNumber).join('\n');
  return value.map(row => row.map(fmtNumber).join('\t')).join('\n');
}

// Points to plot: [x, y, row index]. A list: position against value. A matrix: column
// xCol (or the position, when xCol is -1) against column yCol; rows missing either are left out.
export function seriesPoints(type, value, xCol = -1, yCol = 0) {
  const out = [];
  if (!Array.isArray(value)) return out;
  value.forEach((entry, i) => {
    const x = type === 'list' || xCol < 0 ? i + 1 : entry[xCol];
    const y = type === 'list' ? entry : entry[yCol];
    if (x != null && y != null) out.push([x, y, i]);
  });
  return out;
}

// Copy text (tab-separated rows) to the clipboard. The clipboard API needs HTTPS (or
// localhost) and the browser's permission; where either is missing, the older copy command.
export async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (e) {
      /* not allowed here: the older way below */
    }
  }
  const area = Object.assign(document.createElement('textarea'), { value: text, readOnly: true });
  area.style.cssText = 'position:fixed;top:-1000px;opacity:0';
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand('copy');
  area.remove();
  if (!ok) throw new Error("The browser didn't allow copying.");
}

// -- the scatter plot -------------------------------------------------------------
// Up to three series take the first three slots of the validated categorical palette
// (distinct for every pair, colour-blind safe on white). More series share one hue and the
// one under the pointer (or in the legend) is drawn on top, the rest in grey.
const PALETTE = ['#2a78d6', '#eb6834', '#1baf7a'];
const REST = '#c3c2b7';
const SURFACE = '#ffffff';
const HIT = 24;                            // px: the pointer only has to be this close to a point

export function seriesColor(index, count) {
  return count > PALETTE.length ? PALETTE[0] : PALETTE[index % PALETTE.length];
}

function niceStep(span, count) {
  const raw = span / Math.max(1, count);
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * power;
}

function decimalsOf(step) {
  return Math.max(0, Math.min(12, -Math.floor(Math.log10(step) + 1e-9)));
}

function scaleFor(lo, hi, count) {
  if (lo === hi) {
    const pad = Math.abs(lo) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  } else {                                   // keep points off the axis lines, but don't cross zero for it
    const pad = (hi - lo) * 0.02;
    lo = lo >= 0 && lo - pad < 0 ? 0 : lo - pad;
    hi = hi <= 0 && hi + pad > 0 ? 0 : hi + pad;
  }
  const step = niceStep(hi - lo, count);
  const decimals = decimalsOf(step);
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Number(v.toFixed(decimals)));
  const big = Math.max(Math.abs(start), Math.abs(end));
  const format = new Intl.NumberFormat(undefined, big >= 1e7 || (big < 1e-3 && big > 0)
    ? { notation: 'scientific', maximumFractionDigits: 2 } : { maximumFractionDigits: decimals });
  return { lo: start, hi: end, ticks, label: v => format.format(v) };
}

const SVG = 'http://www.w3.org/2000/svg';

function svgEl(name, attrs = {}, text) {
  const el = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text != null) el.textContent = text;
  return el;
}

export class ScatterPlot {
  // container: an element the plot fills (its width; the height given here).
  constructor(container, { height = 320, compact = false } = {}) {
    Object.assign(this, { container, height, compact });
    this.data = { series: [] };
    this.active = null;                    // the series drawn on top, or null
    this.focus = null;                     // [series, point] the keyboard or pointer is on
    container.classList.add('atlas-plot');
    container.tabIndex = 0;
    container.setAttribute('role', 'img');
    container.innerHTML = '';
    this.svg = container.appendChild(svgEl('svg', { class: 'atlas-plot-axes', 'aria-hidden': 'true' }));
    this.canvas = container.appendChild(document.createElement('canvas'));
    this.canvas.className = 'atlas-plot-points';
    this.over = container.appendChild(svgEl('svg', { class: 'atlas-plot-over', 'aria-hidden': 'true' }));
    this.tip = container.appendChild(document.createElement('div'));
    this.tip.className = 'atlas-plot-tip';
    this.tip.hidden = true;
    this.empty = container.appendChild(document.createElement('div'));
    this.empty.className = 'atlas-plot-empty';
    this.onMove = e => this.pointer(e);
    this.onLeave = () => this.pick(null);
    this.onKey = e => this.key(e);
    container.addEventListener('pointermove', this.onMove);
    container.addEventListener('pointerleave', this.onLeave);
    container.addEventListener('keydown', this.onKey);
    container.addEventListener('blur', this.onLeave);
    this.observer = new ResizeObserver(() => this.render());
    this.observer.observe(container);
  }

  destroy() {
    this.observer.disconnect();
    this.container.removeEventListener('pointermove', this.onMove);
    this.container.removeEventListener('pointerleave', this.onLeave);
    this.container.removeEventListener('keydown', this.onKey);
    this.container.removeEventListener('blur', this.onLeave);
    this.container.innerHTML = '';
  }

  // data: {series: [{name, points: [[x, y, row], …]}], xLabel, yLabel, rowLabel, emptyText}
  set(data) {
    this.data = data;
    this.active = null;
    this.focus = null;
    const n = data.series.reduce((sum, s) => sum + s.points.length, 0);
    this.container.setAttribute('aria-label', n
      ? `Scatter plot of ${data.yLabel} against ${data.xLabel}: ${n.toLocaleString()} points`
        + (data.series.length > 1 ? ` in ${data.series.length} series` : '')
      : data.emptyText || 'Nothing to plot');
    this.render();
  }

  // Draw one series on top (from the legend); null for none.
  highlight(index) {
    if (this.active === index) return;
    this.active = index;
    this.drawPoints();
  }

  color(index) {
    const count = this.data.series.length;
    if (count > PALETTE.length && this.active != null && index !== this.active) return REST;
    return seriesColor(index, count);
  }

  render() {
    const width = this.container.clientWidth;
    if (!width) return;                     // hidden (a modal not shown yet): the observer calls again
    const { series } = this.data;
    const total = series.reduce((sum, s) => sum + s.points.length, 0);
    this.svg.replaceChildren();
    this.over.replaceChildren();
    this.tip.hidden = true;
    this.container.style.height = `${this.height}px`;
    for (const el of [this.svg, this.over]) {
      el.setAttribute('width', width);
      el.setAttribute('height', this.height);
    }
    this.empty.textContent = total ? '' : (this.data.emptyText || 'Nothing to plot');
    this.empty.hidden = !!total;
    if (!total) {
      this.canvas.width = 0;
      this.layout = null;
      return;
    }
    let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
    for (const s of series) {
      for (const [x, y] of s.points) {
        if (x < xMin) xMin = x;
        if (x > xMax) xMax = x;
        if (y < yMin) yMin = y;
        if (y > yMax) yMax = y;
      }
    }
    const style = getComputedStyle(this.container);
    const font = `11px ${style.fontFamily}`;
    const measure = document.createElement('canvas').getContext('2d');
    measure.font = font;
    const ys = scaleFor(yMin, yMax, this.compact ? 4 : 6);
    const labelWidth = Math.max(...ys.ticks.map(v => measure.measureText(ys.label(v)).width));
    const m = { left: Math.ceil(labelWidth) + 12, right: 14, top: 22, bottom: 38 };
    const plotW = Math.max(40, width - m.left - m.right);
    const plotH = Math.max(40, this.height - m.top - m.bottom);
    const xs = scaleFor(xMin, xMax, Math.max(2, Math.min(this.compact ? 5 : 8, Math.floor(plotW / 80))));
    const sx = v => m.left + ((v - xs.lo) / (xs.hi - xs.lo)) * plotW;
    const sy = v => m.top + plotH - ((v - ys.lo) / (ys.hi - ys.lo)) * plotH;
    this.layout = { m, plotW, plotH, sx, sy, xs, ys, total };

    // grid and axes: hairlines, the ticks' numbers, the two axis titles
    const grid = svgEl('g', { class: 'atlas-plot-grid' });
    for (const v of xs.ticks) grid.append(svgEl('line', { x1: sx(v), x2: sx(v), y1: m.top, y2: m.top + plotH }));
    for (const v of ys.ticks) grid.append(svgEl('line', { x1: m.left, x2: m.left + plotW, y1: sy(v), y2: sy(v) }));
    const axes = svgEl('g', { class: 'atlas-plot-axis' });
    axes.append(svgEl('line', { x1: m.left, x2: m.left + plotW, y1: m.top + plotH, y2: m.top + plotH }),
                svgEl('line', { x1: m.left, x2: m.left, y1: m.top, y2: m.top + plotH }));
    const ticks = svgEl('g', { class: 'atlas-plot-ticks' });
    for (const v of xs.ticks) {
      ticks.append(svgEl('text', { x: sx(v), y: m.top + plotH + 15, 'text-anchor': 'middle' }, xs.label(v)));
    }
    for (const v of ys.ticks) {
      ticks.append(svgEl('text', { x: m.left - 7, y: sy(v) + 3.5, 'text-anchor': 'end' }, ys.label(v)));
    }
    const titles = svgEl('g', { class: 'atlas-plot-titles' });
    titles.append(svgEl('text', { x: m.left + plotW / 2, y: this.height - 4, 'text-anchor': 'middle' }, this.data.xLabel),
                  svgEl('text', { x: 2, y: 12 }, this.data.yLabel));
    this.svg.append(grid, axes, ticks, titles);

    // points on a canvas (thousands stay quick); an index of them for the pointer
    const ratio = window.devicePixelRatio || 1;
    Object.assign(this.canvas.style, { left: `${m.left}px`, top: `${m.top}px`, width: `${plotW}px`, height: `${plotH}px` });
    this.canvas.width = Math.round(plotW * ratio);
    this.canvas.height = Math.round(plotH * ratio);
    this.radius = total <= 300 ? 4 : total <= 2000 ? 3 : 2;
    this.ring = total <= 2000;
    this.cells = new Map();
    series.forEach((s, si) => s.points.forEach((p, pi) => {
      const px = sx(p[0]);
      const py = sy(p[1]);
      const cell = `${Math.floor(px / HIT)},${Math.floor(py / HIT)}`;
      if (!this.cells.has(cell)) this.cells.set(cell, []);
      this.cells.get(cell).push([px, py, si, pi]);
    }));
    this.drawPoints();
    if (this.focus) this.pick(this.focus);
  }

  drawPoints() {
    if (!this.layout) return;
    const { m, sx, sy } = this.layout;
    const ctx = this.canvas.getContext('2d');
    const ratio = this.canvas.width / this.layout.plotW;
    ctx.setTransform(ratio, 0, 0, ratio, -m.left * ratio, -m.top * ratio);
    ctx.clearRect(m.left, m.top, this.layout.plotW, this.layout.plotH);
    const { series } = this.data;
    const crowd = series.length > PALETTE.length;
    const order = series.map((_, i) => i).filter(i => i !== this.active);
    if (this.active != null) order.push(this.active);
    const r = this.radius;
    for (const si of order) {
      ctx.globalAlpha = crowd ? (this.active == null ? 0.6 : si === this.active ? 1 : 0.45) : 1;
      for (const [x, y] of series[si].points) {
        const px = sx(x);
        const py = sy(y);
        ctx.beginPath();
        if (this.ring) {
          ctx.fillStyle = SURFACE;
          ctx.arc(px, py, r + 1.5, 0, Math.PI * 2);
          ctx.fill();
          ctx.beginPath();
        }
        ctx.fillStyle = this.color(si);
        ctx.arc(px, py, r, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
  }

  pointer(e) {
    if (!this.layout) return;
    const box = this.container.getBoundingClientRect();
    const px = e.clientX - box.left;
    const py = e.clientY - box.top;
    const cx = Math.floor(px / HIT);
    const cy = Math.floor(py / HIT);
    let best = null;
    let bestD = HIT * HIT;
    for (let i = cx - 1; i <= cx + 1; i += 1) {
      for (let j = cy - 1; j <= cy + 1; j += 1) {
        for (const hit of this.cells.get(`${i},${j}`) || []) {
          const d = (hit[0] - px) ** 2 + (hit[1] - py) ** 2;
          if (d < bestD || (d === bestD && hit[2] === this.active)) {
            bestD = d;
            best = hit;
          }
        }
      }
    }
    this.pick(best ? [best[2], best[3]] : null);
  }

  // Show one point: a ring around it and its values; [series, point] or null.
  pick(at) {
    this.focus = at;
    this.over.replaceChildren();
    if (!at || !this.layout) {
      this.tip.hidden = true;
      if (this.data.series.length > PALETTE.length) this.highlight(null);
      return;
    }
    const [si, pi] = at;
    const s = this.data.series[si];
    const p = s.points[pi];
    const { sx, sy } = this.layout;
    const x = sx(p[0]);
    const y = sy(p[1]);
    if (this.data.series.length > PALETTE.length) this.highlight(si);
    const color = this.color(si);
    this.over.append(svgEl('circle', { cx: x, cy: y, r: this.radius + 5, fill: 'none', stroke: color, 'stroke-opacity': 0.35, 'stroke-width': 2 }),
                     svgEl('circle', { cx: x, cy: y, r: this.radius + 1.5, fill: color, stroke: SURFACE, 'stroke-width': 2 }));
    this.tip.replaceChildren();
    if (this.data.series.length > 1) {
      const head = this.tip.appendChild(document.createElement('div'));
      head.className = 'atlas-plot-tip-series';
      const keyLine = head.appendChild(document.createElement('span'));
      keyLine.className = 'atlas-plot-key';
      keyLine.style.background = color;
      head.append(s.name);
    }
    const rowLabel = this.data.rowLabel || 'Row';
    const lines = [[fmtNumber(p[0]), this.data.xLabel], [fmtNumber(p[1]), this.data.yLabel]];
    if (this.data.xLabel !== rowLabel) lines.push([(p[2] + 1).toLocaleString(), rowLabel]);  // else x is the position
    for (const [value, label] of lines) {        // the value leads, its name follows
      const line = this.tip.appendChild(document.createElement('div'));
      line.appendChild(document.createElement('strong')).textContent = value;
      line.appendChild(document.createElement('span')).textContent = label;
    }
    this.tip.hidden = false;
    const box = this.container.getBoundingClientRect();
    const tw = this.tip.offsetWidth;
    const th = this.tip.offsetHeight;
    const left = x + 14 + tw > box.width ? x - 14 - tw : x + 14;
    const top = Math.min(Math.max(4, y - th / 2), this.height - th - 4);
    this.tip.style.left = `${Math.max(4, left)}px`;
    this.tip.style.top = `${top}px`;
  }

  // Arrow keys walk the points (left/right) and the series (up/down); Escape lets go.
  key(e) {
    const { series } = this.data;
    if (!this.layout || !series.length) return;
    let [si, pi] = this.focus || [0, -1];
    if (e.key === 'ArrowRight') pi += 1;
    else if (e.key === 'ArrowLeft') pi -= 1;
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      si = (si + (e.key === 'ArrowDown' ? 1 : series.length - 1)) % series.length;
      pi = Math.min(Math.max(pi, 0), series[si].points.length - 1);
    } else if (e.key === 'Escape') {
      this.pick(null);
      return;
    } else return;
    e.preventDefault();
    if (!series[si].points.length) return;
    pi = Math.min(Math.max(pi, 0), series[si].points.length - 1);
    this.pick([si, pi]);
  }
}
