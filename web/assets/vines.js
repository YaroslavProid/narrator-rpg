// Procedural ivy for the screen corners: tapered stems with branches, ivy and lance leaves (some withered),
// thorns, curling tendrils and dark berries. Grows in on load, then sways slightly.
const NS = 'http://www.w3.org/2000/svg';

function rand(seed) {  // mulberry32
  return () => {
    seed = seed + 0x6D2B79F5 | 0;
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}
const f = n => Math.round(n * 10) / 10;
const pt = p => `${f(p[0])},${f(p[1])}`;

function smooth(pts, closed = false) {
  let d = `M${pt(pts[0])}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const m = [(pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2];
    d += `Q${pt(pts[i])} ${pt(m)}`;
  }
  d += `L${pt(pts[pts.length - 1])}`;
  return closed ? d + 'Z' : d;
}

function el(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}

// stem centreline: heading `ang`, wanders but keeps returning to `target` (hugs the screen edge)
function stem(r, x, y, ang, len, step, wander, target, pull) {
  const pts = [[x, y, ang]];
  let a = ang, cv = 0;
  for (let d = 0; d < len; d += step) {
    cv = cv * 0.8 + (r() - 0.5) * wander;
    a += cv + (target - a) * pull;
    x += Math.cos(a) * step; y += Math.sin(a) * step;
    pts.push([x, y, a]);
  }
  return pts;
}

function ribbon(pts, w0, w1) {
  const L = [], R = [], n = pts.length - 1;
  pts.forEach(([x, y, a], i) => {
    const w = (w0 + (w1 - w0) * Math.pow(i / n, 0.65)) / 2;
    const nx = -Math.sin(a), ny = Math.cos(a);
    L.push([x + nx * w, y + ny * w]); R.push([x - nx * w, y - ny * w]);
  });
  return smooth(L.concat(R.reverse()), true);
}

const LEAF = {
  ivy: s => `M0,0C${f(.1 * s)},${f(-.28 * s)} ${f(.28 * s)},${f(-.52 * s)} ${f(.45 * s)},${f(-.44 * s)}` +
    `C${f(.56 * s)},${f(-.39 * s)} ${f(.54 * s)},${f(-.24 * s)} ${f(.62 * s)},${f(-.21 * s)}` +
    `C${f(.82 * s)},${f(-.2 * s)} ${f(s)},${f(-.06 * s)} ${f(s)},0` +
    `C${f(s)},${f(.06 * s)} ${f(.82 * s)},${f(.2 * s)} ${f(.62 * s)},${f(.21 * s)}` +
    `C${f(.54 * s)},${f(.24 * s)} ${f(.56 * s)},${f(.39 * s)} ${f(.45 * s)},${f(.44 * s)}` +
    `C${f(.28 * s)},${f(.52 * s)} ${f(.1 * s)},${f(.28 * s)} 0,0Z`,
  lance: s => `M0,0C${f(.25 * s)},${f(-.3 * s)} ${f(.7 * s)},${f(-.26 * s)} ${f(s)},0` +
    `C${f(.7 * s)},${f(.26 * s)} ${f(.25 * s)},${f(.3 * s)} 0,0Z`,
};

function corner(seed, size, opts) {
  const r = rand(seed);
  const svg = el('svg', { viewBox: `0 0 ${size} ${size}`, 'aria-hidden': 'true' });
  const id = 'v' + seed;
  const defs = el('defs', {}, svg);
  const grad = (gid, a, b) => {
    const g = el('linearGradient', { id: gid, x1: '0', y1: '0', x2: '1', y2: '1' }, defs);
    el('stop', { offset: '0', 'stop-color': a }, g); el('stop', { offset: '1', 'stop-color': b }, g);
  };
  grad(id + 'l0', '#56653a', '#1f2914');
  grad(id + 'l1', '#4a5a30', '#18210f');
  grad(id + 'l2', '#63703f', '#2a3419');
  grad(id + 'ld', '#6e5530', '#2e2210');   // withered
  grad(id + 'st', '#3a3020', '#1a150c');
  const g = el('g', { class: 'vine-sway' }, svg);
  const back = el('g', { class: 'vine-back' }, g);
  let stems = el('g', {}, back), leaves = el('g', {}, back);
  const dur = opts.grow, items = [];

  function branch(x, y, ang, len, width, depth, t0, target, pull) {
    const step = 7;
    const pts = stem(r, x, y, ang, len, step, 0.16, target, pull);
    const n = pts.length - 1;
    const span = dur * (len / opts.longest);
    // tapered body revealed by an animated mask along the centreline
    const mid = 'm' + seed + '_' + items.length;
    const mask = el('mask', { id: mid, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: size, height: size }, defs);
    el('path', { d: smooth(pts), class: 'vine-reveal', fill: 'none', stroke: '#fff', 'stroke-width': width + 8,
                 'stroke-linecap': 'round', pathLength: 1, style: `animation-delay:${t0}ms;animation-duration:${span}ms` }, mask);
    el('path', { d: ribbon(pts, width, 0.6), fill: `url(#${id}st)`, mask: `url(#${mid})` }, stems);
    items.push(1);
    let side = r() < 0.5 ? 1 : -1, next = 1 + Math.floor(r() * 2);
    for (let i = 2; i <= n; i++) {
      const [px, py, a] = pts[i], t = i / n, delay = t0 + span * t;
      if (i >= next) {
        next = i + 1 + Math.floor(r() * 2);
        side = r() < 0.75 ? -side : side;
        const s = opts.leaf * (1.05 - t * 0.55) * (0.7 + r() * 0.45) * (depth ? 0.8 : 1);
        const dead = r() < 0.14, kind = r() < 0.72 ? 'ivy' : 'lance';
        const la = a + side * (0.55 + r() * 0.7);
        const lg = el('g', { transform: `translate(${f(px)},${f(py)}) rotate(${f(la * 180 / Math.PI)})` }, leaves);
        const fill = dead ? 'ld' : 'l' + Math.floor(r() * 3);
        const leaf = el('g', { class: 'vine-leaf', style: `animation-delay:${Math.round(delay)}ms` }, lg);
        el('path', { d: `M0,0L${f(s * .16)},0`, stroke: '#2a2414', 'stroke-width': 1.2, fill: 'none' }, leaf);
        el('path', { d: LEAF[kind](s), transform: `translate(${f(s * .14)},0)`, fill: `url(#${id}${fill})`,
                     stroke: dead ? '#3a2a12' : '#151b0c', 'stroke-width': 0.6 }, leaf);
        el('path', { d: `M${f(s * .16)},0Q${f(s * .6)},${f(side * s * .04)} ${f(s * 1.02)},0`, stroke: dead ? '#8a6c3c' : '#7d8c55',
                     'stroke-width': 0.7, fill: 'none', opacity: 0.55 }, leaf);
      }
      if (r() < 0.05 && width * (1 - t) > 2) {   // thorn
        const ts = -side, nx = -Math.sin(a) * ts, ny = Math.cos(a) * ts, w = width * (1 - t) / 2;
        const b1 = [px + nx * w - Math.cos(a) * 2.5, py + ny * w - Math.sin(a) * 2.5];
        const b2 = [px + nx * w + Math.cos(a) * 2.5, py + ny * w + Math.sin(a) * 2.5];
        const tip = [px + nx * (w + 5) + Math.cos(a) * 3, py + ny * (w + 5) + Math.sin(a) * 3];
        el('path', { d: `M${pt(b1)}L${pt(tip)}L${pt(b2)}Z`, fill: '#241d10', class: 'vine-leaf',
                     style: `animation-delay:${Math.round(delay)}ms` }, leaves);
      }
      if (r() < 0.035 && t > 0.25) {             // curling tendril
        let tx = px, ty = py, ta = a + side * 1.1, k = 0.22 + r() * 0.12, tp = [[tx, ty]];
        for (let j = 0; j < 26; j++) { ta += side * k; k *= 1.07; const st = 3.2 * (1 - j / 32); tx += Math.cos(ta) * st; ty += Math.sin(ta) * st; tp.push([tx, ty]); }
        el('path', { d: smooth(tp), stroke: '#33301c', 'stroke-width': 1, fill: 'none', 'stroke-linecap': 'round',
                     class: 'vine-leaf', style: `animation-delay:${Math.round(delay)}ms` }, leaves);
      }
      if (r() < 0.02 && t > 0.3) {               // berries
        const bg = el('g', { class: 'vine-leaf', style: `animation-delay:${Math.round(delay + 200)}ms` }, leaves);
        const c = 2 + Math.floor(r() * 3);
        for (let j = 0; j < c; j++) {
          const bx = px + (r() - .5) * 9, by = py + (r() - .5) * 9 + 3, br = 2.4 + r() * 1.3;
          el('circle', { cx: f(bx), cy: f(by), r: f(br), fill: '#2b0a10', stroke: '#14050a', 'stroke-width': .5 }, bg);
          el('circle', { cx: f(bx - br * .35), cy: f(by - br * .35), r: f(br * .3), fill: '#7a3040', opacity: .7 }, bg);
        }
      }
      if (depth > 0 && i > n * 0.12 && i < n * 0.75 && r() < 0.07) {
        const ba = a + (r() < 0.5 ? -1 : 1) * (0.5 + r() * 0.5);
        branch(px, py, ba, (len - i * step) * (0.35 + r() * 0.25), Math.max(1.5, width * (1 - t) * 0.7), depth - 1,
               delay, ba + (target - ba) * 0.5, pull * 0.5);
      }
    }
  }

  const L = size * 0.95, H = Math.PI / 2;
  opts.longest = L;
  // back layer (darker, for depth): a second pair along the edges
  branch(-6, 20 + r() * 10, 0.12, L * 0.8, opts.width * 0.8, 1, 300, 0.2, 0.06);
  branch(22 + r() * 10, -6, H - 0.12, L * 0.7, opts.width * 0.8, 1, 400, H - 0.2, 0.06);
  stems = el('g', {}, g); leaves = el('g', {}, g);
  // front: along the top edge, along the side edge, a short one into the diagonal, and hanging strands
  branch(-6, 6 + r() * 8, 0.05, L * (0.85 + r() * 0.15), opts.width, 2, 0, 0.12, 0.07);
  branch(6 + r() * 8, -6, H - 0.05, L * (0.75 + r() * 0.2), opts.width * 0.9, 2, 120, H - 0.1, 0.07);
  branch(0, 0, Math.PI / 4 + (r() - .5) * .3, L * (0.42 + r() * 0.12), opts.width * 0.85, 1, 260, Math.PI / 4, 0.03);
  for (const x of [0.32 + r() * 0.08, 0.55 + r() * 0.1]) {
    if (opts.hang) branch(size * x, 4, H + (r() - .5) * .4, L * opts.hang * (0.7 + r() * 0.4), opts.width * 0.55, 1, 900 + r() * 600, H, 0.09);
  }
  return svg;
}

export function plantVines(root, seed = 7) {
  root.innerHTML = '';
  const corners = [['tl', 1.0], ['tr', 1.0], ['bl', 0.62], ['br', 0.62]];
  corners.forEach(([pos, scale], i) => {
    const box = document.createElement('div');
    box.className = 'vines ' + pos;
    box.style.setProperty('--scale', scale);
    box.appendChild(corner(seed * 31 + i * 977, 420, { grow: 4200, leaf: 30, width: 9, hang: pos[0] === 't' ? 0.45 : 0 }));
    root.appendChild(box);
  });
}
