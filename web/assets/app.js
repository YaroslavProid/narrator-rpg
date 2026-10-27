// Narrator RPG — client. Vanilla ES module: shelf, new chronicle, open book (story + hero sheet).
import { plantVines } from './vines.js';

/* ============ utilities ============ */
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const LS = {
  get(k, d) { try { const v = localStorage.getItem('nrpg.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('nrpg.' + k, JSON.stringify(v)); } catch {} },
};
const hue = s => { let h = 0; for (const c of String(s)) h = (h * 31 + c.codePointAt(0)) % 360; return h; };
const inkOf = name => `hsl(${hue(name)} 45% 72%)`;
const initial = s => (String(s || '?').trim()[0] || '?').toUpperCase();
const plural = (n, f) => f[(n % 10 === 1 && n % 100 !== 11) ? 0 : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? 1 : 2];
const mod = v => { const m = Math.floor((v - 10) / 2); return m > 0 ? '+' + m : m < 0 ? '−' + -m : '+0'; };
const pct = (c, m) => m ? Math.max(0, Math.min(100, Math.round(100 * c / m))) : 0;
function roman(n) {
  if (!(n > 0 && n < 4000)) return String(n);
  const t = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'], [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];
  let s = ''; for (const [v, r] of t) while (n >= v) { s += r; n -= v; } return s;
}
function ago(str) {
  const d = new Date(String(str).replace(' ', 'T')); if (isNaN(d)) return str;
  const s = (Date.now() - d) / 1000;
  if (s < 60) return 'только что';
  if (s < 3600) { const n = Math.floor(s / 60); return `${n} ${plural(n, ['минуту', 'минуты', 'минут'])} назад`; }
  if (s < 86400) { const n = Math.floor(s / 3600); return `${n} ${plural(n, ['час', 'часа', 'часов'])} назад`; }
  if (s < 172800) return 'вчера';
  if (s < 1209600) { const n = Math.floor(s / 86400); return `${n} ${plural(n, ['день', 'дня', 'дней'])} назад`; }
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
}
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  let j = null; try { j = await r.json(); } catch {}
  if (!r.ok) throw new Error(j && (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j)) || r.statusText);
  return j;
}
function toast(text, err = true, ms = 5000) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : ''); t.textContent = text;
  $('#toasts').appendChild(t); setTimeout(() => t.remove(), ms);
}
function ask({ title, text = '', ok = 'Да', cancel = 'Оставить как есть', input = null }) {
  const dlg = $('#dlg'), f = $('#dlgForm');
  f.innerHTML = `<h2>${esc(title)}</h2>${text ? `<p>${esc(text)}</p>` : ''}
    ${input != null ? `<input class="line" id="dlgIn" value="${esc(input)}">` : ''}
    <div class="acts"><button class="dlg-btn red" value="ok">${esc(ok)}</button><button class="dlg-btn" value="cancel">${esc(cancel)}</button></div>`;
  return new Promise(res => {
    dlg.onclose = () => res(dlg.returnValue === 'ok' ? (input != null ? $('#dlgIn').value : true) : null);
    dlg.returnValue = ''; dlg.showModal();
    if (input != null) $('#dlgIn').focus();
  });
}
const QUILL = (color = 'currentColor', w = 22, h = 26) => `<svg aria-hidden="true" width="${w}" height="${h}" viewBox="0 0 22 26" style="color:${color}"><use href="#quill"/></svg>`;

/* ============ preferences & atmosphere ============ */
const prefs = { size: LS.get('size', 'm'), vines: LS.get('vines', true), dust: LS.get('dust', true), candle: LS.get('candle', true), meta: LS.get('meta', true) };
function applyPrefs() {
  const b = document.body;
  b.classList.toggle('ts-s', prefs.size === 's'); b.classList.toggle('ts-l', prefs.size === 'l');
  for (const k of ['vines', 'dust', 'candle', 'meta']) b.classList.toggle('no-' + k, !prefs[k]);
  $$('[data-size]').forEach(x => x.classList.toggle('on', x.dataset.size === prefs.size));
  $$('[data-fx]').forEach(x => { x.checked = !!prefs[x.dataset.fx]; });
}
function setPref(k, v) { prefs[k] = v; LS.set(k, v); applyPrefs(); if (k === 'vines' && v && !$('#vines').children.length) plantVines($('#vines')); }
$$('[data-size]').forEach(x => x.onclick = () => setPref('size', x.dataset.size));
$$('[data-fx]').forEach(x => x.onchange = () => setPref(x.dataset.fx, x.checked));
$('#dust').innerHTML = Array.from({ length: 12 }, (_, i) =>
  `<span class="mote" style="left:${(i * 37 + 7) % 97}%;top:${(i * 53 + 20) % 90}%;animation-delay:-${(i * 2.3).toFixed(1)}s;animation-duration:${16 + (i % 5) * 2}s"></span>`).join('');
if (prefs.vines) plantVines($('#vines'));

/* ============ LLM status = candle ============ */
const CANDLE = { ok: ['на связи', ''], loading: ['загружается', 'load'], down: ['недоступен', 'off'], error: ['ошибка', 'off'] };
async function pollStatus() {
  let s;
  try { s = await api('api/status'); } catch { s = { llm: 'down', llm_url: 'сервер narrator не отвечает' }; }
  const [lbl, cls] = CANDLE[s.llm] || ['?', 'off'];
  $('#candle').setAttribute('class', 'candle ' + cls);
  $('#candLbl').textContent = lbl;
  $('#candTitle').textContent = `Рассказчик: ${lbl} (${s.llm_url || ''})` + (s.memory === false ? ' · векторная память выключена' : '');
}
pollStatus(); setInterval(pollStatus, 20000);

/* ============ router ============ */
function route() {
  const h = location.hash.replace(/^#\/?/, '');
  if (h.startsWith('s/')) return openScene(decodeURIComponent(h.slice(2)));
  closeSocket();
  if (h === 'new') return showNew();
  return showShelf();
}
window.addEventListener('hashchange', route);
function setView(v) {
  $$('.view').forEach(x => x.classList.toggle('on', x.id === v));
  $('#bBack').hidden = v === 'vShelf';
  $('#vitals').hidden = v !== 'vGame';
  $('#netBanner').hidden = true;
  if (v !== 'vGame') { closeSheet(); setTitle('Narrator RPG', 'летописи'); document.title = 'Narrator RPG'; }
}
function setTitle(t, sub) { $('#title').textContent = t; $('#subtitle').textContent = sub; }
$('#bBack').onclick = () => { location.hash = '#/'; };

/* ============ shelf ============ */
let delId = null;
const LEATHERS = [['#5a1d16', '#2e0d09'], ['#2b3b25', '#141d11'], ['#25304a', '#10151f'], ['#4a3220', '#20140a'], ['#3e2238', '#1a0d17'], ['#4a3a1c', '#1f170a']];
async function showShelf() {
  setView('vShelf');
  let list;
  try { list = await api('api/scenes'); }
  catch (e) { $('#shelf').innerHTML = `<div class="empty"><h2>Рассказчик не отвечает</h2><p>${esc(e.message)}</p></div>`; return; }
  renderShelf(list);
}
function renderShelf(list) {
  $('#shelfLede').hidden = !list.length;
  if (!list.length) {
    $('#shelf').innerHTML = `<div class="empty">
      <div class="shelf" style="margin-top:40px"><div class="slot" style="flex-basis:300px">
        <div class="bk blank" style="transform:rotate(-4deg)"><a class="cv" href="#/new">${QUILL('var(--ink)', 50, 58)}<b>Новая<br>летопись</b></a></div>
        <div class="board"></div></div></div>
      <h2>Полка пуста</h2>
      <p>Здесь пока не написано ни одной летописи. Придумай мир и героя — рассказчик сотворит остальное.</p>
      <a class="go" href="#/new"><span class="wax" aria-hidden="true" style="width:46px;height:44px"><svg width="20" height="22" viewBox="0 0 22 26" style="color:rgba(0,0,0,.5)"><use href="#quill"/></svg></span>Начать первую историю</a></div>`;
    return;
  }
  const book = s => {
    const [c1, c2] = LEATHERS[hue(s.title) % LEATHERS.length];
    const confirming = delId === s.id;
    return `<div class="slot"><div class="bk${confirming ? ' lift' : ''}" style="--c1:${c1};--c2:${c2}">
      <a class="cv" href="#/s/${encodeURIComponent(s.id)}" aria-label="Открыть летопись «${esc(s.title)}»">
        <svg class="tex" aria-hidden="true"><rect width="100%" height="100%" filter="url(#lth)"/></svg>
        <span class="bands" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <span class="frame">
          <svg class="cap" style="top:-5px;left:-5px"><use href="#cap"/></svg><svg class="cap" style="top:-5px;right:-5px;transform:scaleX(-1)"><use href="#cap"/></svg>
          <svg class="cap" style="bottom:-5px;left:-5px;transform:scaleY(-1)"><use href="#cap"/></svg><svg class="cap" style="bottom:-5px;right:-5px;transform:scale(-1,-1)"><use href="#cap"/></svg>
          <svg aria-hidden="true" width="40" height="14" viewBox="0 0 40 14"><path d="M2 7 H 14 M26 7 H 38 M20 2 L 24 7 L 20 12 L 16 7 Z" fill="none" stroke="#c9a45c" stroke-width="1"/></svg>
          <span class="t">${esc(s.title)}</span>
          <span class="mtag">${s.rule_mode === 'd20' ? 'd20' : 'Система'}</span>
          <span class="turns">${s.turn} ${plural(s.turn, ['ход', 'хода', 'ходов'])}</span>
          <span class="when">${esc(ago(s.updated))}</span>
        </span>
      </a>
      <button class="del" data-del="${esc(s.id)}" aria-label="Удалить летопись «${esc(s.title)}»">Удалить</button>
      ${confirming ? `<div class="ask" role="alertdialog" aria-label="Подтверждение удаления">
        <p>Сжечь летопись «${esc(s.title)}»? Её нельзя будет вернуть.</p>
        <div><button class="dlg-btn" data-keep>Оставить</button><button class="dlg-btn red" data-burn="${esc(s.id)}">Сжечь</button></div></div>` : ''}
    </div><div class="board"></div></div>`;
  };
  $('#shelf').innerHTML = `<div class="shelf">${list.map(book).join('')}
    <div class="slot"><div class="bk blank"><a class="cv" href="#/new">${QUILL('var(--ink)', 70, 80)}<b>Новая летопись</b><span>чистые страницы ждут</span></a></div><div class="board"></div></div></div>
    <p class="shelf-foot">Наведи на книгу — она приоткроется. Летописи хранятся на этом компьютере.</p>`;
  $('#shelf')._list = list;
}
$('#shelf').addEventListener('click', async e => {
  const del = e.target.closest('[data-del]'), keep = e.target.closest('[data-keep]'), burn = e.target.closest('[data-burn]');
  if (del) { e.preventDefault(); delId = del.dataset.del; renderShelf($('#shelf')._list); }
  else if (keep) { delId = null; renderShelf($('#shelf')._list); }
  else if (burn) {
    try { await api('api/scenes/' + encodeURIComponent(burn.dataset.burn), { method: 'DELETE' }); toast('Летопись сожжена', false, 2500); }
    catch (err) { toast('Не удалось удалить: ' + err.message); }
    delId = null; showShelf();
  }
});

/* ============ new chronicle ============ */
const IDEAS = [
  ['мрачное фэнтези', 'Таверна у брода, откуда никто не возвращается. Хозяйка знает больше, чем говорит, а за героем идут люди, которым он задолжал.'],
  ['LitRPG', 'Мир, где у каждого над головой висит уровень. У тебя — ноль, и Система почему-то молчит.'],
  ['киберпанк', 'Нижний город, вечный дождь и долг корпорации, записанный прямо в позвоночник.'],
  ['космоопера', 'Ржавый грузовоз на краю обитаемого космоса и груз, о котором лучше не спрашивать.'],
  ['хоррор', 'Дом на болотах, куда ты вернулся после похорон отца. Ночью в стенах кто-то ходит.'],
  ['детектив', 'Портовый город, пропавшая дочь судьи и сыщик, которому уже нечего терять.'],
];
let ruleMode = 'd20', npcExtra = 0, draft = null, genTimer = null;
$('#ideas').innerHTML = IDEAS.map(([l], i) => `<button class="idea" data-idea="${i}" aria-pressed="false">${esc(l)}</button>`).join('');
$('#ideas').onclick = e => {
  const b = e.target.closest('[data-idea]'); if (!b) return;
  $('#nPremise').value = IDEAS[b.dataset.idea][1];
  $$('.idea').forEach(x => { const on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on); });
};
$('#nPremise').oninput = () => $$('.idea').forEach(x => { x.classList.remove('on'); x.setAttribute('aria-pressed', false); });
$('#modes').onclick = e => {
  const b = e.target.closest('[data-mode]'); if (!b) return;
  ruleMode = b.dataset.mode;
  $$('#modes .mode').forEach(x => { const on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on); });
};
const setExtra = n => { npcExtra = Math.max(0, Math.min(8, n)); $('#npcCount').textContent = npcExtra; };
$('#npcMinus').onclick = () => setExtra(npcExtra - 1);
$('#npcPlus').onclick = () => setExtra(npcExtra + 1);
let npcK = 0;
function addNpc(name = '', desc = '') {
  const k = ++npcK, el = document.createElement('div');
  el.className = 'npc-row';
  el.innerHTML = `<label class="sr" for="nn${k}">Имя</label><input id="nn${k}" class="line nn" value="${esc(name)}" placeholder="имя">
    <label class="sr" for="nd${k}">Кто это</label><input id="nd${k}" class="line nd" value="${esc(desc)}" placeholder="кто это">
    <button class="x" aria-label="Убрать"><svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14"><path d="M2 2 L 12 12 M12 2 L 2 12" stroke="currentColor" stroke-width="1.5"/></svg></button>`;
  el.querySelector('.x').onclick = () => el.remove();
  $('#npcs').appendChild(el);
  return el;
}
$('#bAddNpc').onclick = () => addNpc().querySelector('.nn').focus();
function saveForm() {
  LS.set('newForm', { title: $('#nTitle').value, premise: $('#nPremise').value, name: $('#pName').value, brief: $('#pBrief').value,
    mode: ruleMode, extra: npcExtra,
    npcs: $$('.npc-row').map(r => [r.querySelector('.nn').value, r.querySelector('.nd').value]) });
}
function loadForm() {
  const f = LS.get('newForm', null); if (!f) return false;
  $('#nTitle').value = f.title || ''; $('#nPremise').value = f.premise || ''; $('#pName').value = f.name || ''; $('#pBrief').value = f.brief || '';
  (f.npcs || []).forEach(([n, d]) => addNpc(n, d));
  setExtra(f.extra || 0);
  const mb = $(`#modes [data-mode="${f.mode || 'd20'}"]`); if (mb) mb.click();
  return true;
}
$('#newForm').addEventListener('input', saveForm);
$('#newForm').addEventListener('click', () => setTimeout(saveForm, 0));
function showNew() {
  setView('vNew'); setTitle('Новая летопись', 'сотворение мира');
  $('#newForm').hidden = !!draft; $('#preview').hidden = !draft;
  if (!$('#npcs').children.length && !loadForm()) {
    $('#nTitle').value = ''; $('#pName').value = 'Ярен';
    $('#nPremise').value = IDEAS[0][1]; $$('.idea')[0].classList.add('on');
    $('#pBrief').value = 'Бывший наёмник. Однажды не взял денег — и с тех пор за ним идут.';
    addNpc('Сильфа', 'хозяйка таверны, знает всех путников'); addNpc('Гвоздь', 'главарь шайки, которому должен Ярен');
  }
}
const PHRASES = ['Раскладывает карты местности…', 'Придумывает тайны действующих лиц…', 'Взвешивает силу и ловкость героя…', 'Собирает стартовое снаряжение…', 'Зажигает свечи в таверне…', 'Сверяет имена и судьбы…'];
async function generate() {
  const body = {
    title: $('#nTitle').value.trim(), premise: $('#nPremise').value.trim(), rule_mode: ruleMode,
    player_name: $('#pName').value.trim(), player_brief: $('#pBrief').value.trim(),
    npcs: $$('.npc-row').map(r => ({ name: r.querySelector('.nn').value.trim(), brief: r.querySelector('.nd').value.trim() })).filter(n => n.name || n.brief),
  };
  body.npc_count = npcExtra ? body.npcs.length + npcExtra : 0;   // the server expects the TOTAL; the form asks for "N more"
  $('#gen').hidden = false;
  const t0 = Date.now(); let k = 0;
  genTimer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    $('#genSec').textContent = s;
    if (s && s % 3 === 0) $('#genMsg').textContent = PHRASES[++k % PHRASES.length];
  }, 1000);
  try {
    draft = await api('api/scenes/generate', { method: 'POST', body: JSON.stringify(body) });
    draft._mode = ruleMode; draft._secs = Math.round((Date.now() - t0) / 1000);
    renderPreview(); $('#newForm').hidden = true; $('#preview').hidden = false; $('#vNew').scrollTop = 0;
    setTitle('Титульный лист', `мир сотворён за ${draft._secs} с`);
  } catch (e) { toast('Сотворить мир не вышло: ' + e.message, true, 9000); }
  finally { clearInterval(genTimer); $('#gen').hidden = true; $('#genSec').textContent = '0'; }
}
$('#bGen').onclick = generate;

function dropcapHTML(ch) {
  return `<span class="dropcap" aria-hidden="true">${esc(ch)}<svg width="80" height="80" viewBox="0 0 80 80"><path d="M8 72 C 14 60, 10 50, 16 44 M72 8 C 66 18, 70 26, 62 32 M10 10 C 16 14, 14 20, 9 20 M70 70 C 64 66, 66 60, 71 60" fill="none" stroke="currentColor" stroke-width="1.1"/><circle cx="16" cy="44" r="2" fill="currentColor"/><circle cx="62" cy="32" r="2" fill="currentColor"/></svg></span>`;
}
function prologueHTML(text) {
  const paras = String(text || '').split(/\n\s*\n|\n/).map(s => s.trim()).filter(Boolean);
  return paras.map((p, i) => i === 0
    ? `<p class="narr ni">${dropcapHTML(p[0])}<span class="sr">${esc(p[0])}</span>${esc(p.slice(1))}</p>`
    : `<p class="narr">${esc(p)}</p>`).join('');
}
function attrTable(attrs, d20) {
  const e = Object.entries(attrs || {}); if (!e.length) return '';
  let rows = '';
  for (let i = 0; i < e.length; i += 2) {
    const c = ([k, v]) => `<td>${esc(k)}</td><td class="v">${v}</td><td class="m">${d20 ? mod(v) : ''}</td>`;
    rows += `<tr>${c(e[i])}<td style="width:16px"></td>${e[i + 1] ? c(e[i + 1]) : '<td></td><td></td><td></td>'}</tr>`;
  }
  return `<table class="attr"><tbody>${rows}</tbody></table>`;
}
function worldHTML(w) {
  if (!w || !(w.look || w.rules || (w.places || []).length)) return '';
  const row = (k, v) => v ? `<dt>${k}</dt><dd>${esc(v)}</dd>` : '';
  return `<h2 class="sh">Облик мира</h2><dl class="kv" style="margin-bottom:22px">${row('облик', w.look)}${row('тон', w.tone)}${row('законы', w.rules)}${row('места', (w.places || []).join(' · '))}${row('силы', (w.factions || []).join(' · '))}</dl>`;
}
function renderPreview() {
  const d = draft, p = d.player || {}, d20 = d._mode === 'd20';
  const abil = (p.abilities || []).map(a => `<div class="abil"><span class="nm">${esc(a.name)}</span><span class="kd">${a.kind === 'active' ? 'активная' : 'пассивная'}${a.cost ? ' · ' + esc(a.cost) : ''}${a.cooldown ? ' · кд ' + esc(a.cooldown) : ''}</span>${a.description ? `<span class="ds">${esc(a.description)}</span>` : ''}</div>`).join('');
  const npcs = (d.npcs || []).map((n, i) => `<article style="padding-bottom:18px;border-bottom:1px dotted var(--ink-faint)">
      <div style="display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 12px;margin-bottom:6px"><span style="font:700 23px/1.2 var(--f-sc);color:${inkOf(n.name)}">${esc(n.name)}</span>${n.kind ? `<span style="font-size:16px;font-style:italic;color:var(--ink-soft)">${esc(n.kind)}</span>` : ''}</div>
      <dl class="kv">${n.description ? `<dt>кто</dt><dd>${esc(n.description)}</dd>` : ''}${n.personality ? `<dt>характер</dt><dd>${esc(n.personality)}</dd>` : ''}
      ${n.secrets ? `<dt>тайна</dt><dd data-secret="${i}"><button class="brk" type="button"><span class="wax" aria-hidden="true" style="width:24px;height:23px"></span>сломать печать</button></dd>` : ''}</dl></article>`).join('');
  $('#preview').innerHTML = `<div class="spread">
    <section class="paper torn-l"><svg class="grain"><rect width="100%" height="100%" filter="url(#gm)"/></svg><div class="spine-r"></div>
      <div style="position:relative;max-width:680px;margin:0 auto">
        <header style="text-align:center;padding-bottom:26px;margin-bottom:26px;border-bottom:3px double var(--orn)">
          <h1 style="margin:8px 0 0;font:700 52px/1.05 var(--f-head);color:var(--rubric-hi)">${esc(d.title)}</h1>
          <span class="stamp" style="margin-top:14px">${d20 ? 'жребий · d20' : 'Система'}</span>
        </header>
        ${worldHTML(d.world)}
        <h2 class="sh">Пролог</h2>
        ${prologueHTML(d.setting)}
        <h2 class="sh" style="margin-top:34px">Действующие лица</h2>
        <div style="display:flex;flex-direction:column;gap:20px">${npcs || '<p class="sub">Рассказчик обойдётся без спутников.</p>'}</div>
      </div></section>
    <div class="gut" aria-hidden="true"></div>
    <section class="paper torn-r"><svg class="grain"><rect width="100%" height="100%" filter="url(#gm)"/></svg><div class="spine-l"></div>
      <div style="position:relative;display:flex;flex-direction:column;gap:26px">
        <div style="display:flex;align-items:center;gap:18px">
          <span class="oval lg" aria-hidden="true" style="color:var(--rubric-hi)">${esc(initial(p.name))}</span>
          <div><p style="margin:0;font:700 15px/1 var(--f-sc);letter-spacing:.18em;color:var(--ink-soft)">герой</p>
            <h2 style="margin:0;font:700 38px/1.05 var(--f-head)">${esc(p.name)}</h2>
            <p style="margin:2px 0 0;font-size:18px;font-style:italic;color:var(--ink-soft)">${esc([p.kind, 'уровень 1'].filter(Boolean).join(' · '))}</p></div>
        </div>
        <dl class="kv">${p.description ? `<dt>облик</dt><dd>${esc(p.description)}</dd>` : ''}${p.personality ? `<dt>характер</dt><dd>${esc(p.personality)}</dd>` : ''}</dl>
        <section><h3 class="sh">Атрибуты</h3>${attrTable(p.attrs, d20)}</section>
        ${abil ? `<section><h3 class="sh">Способности</h3><div style="display:flex;flex-direction:column;gap:12px">${abil}</div></section>` : ''}
        ${(p.inventory || []).length ? `<section><h3 class="sh">Снаряжение</h3><dl class="kv kit">${p.inventory.map(i => `<dt>${esc(i)}</dt><dd>${esc((p.inventory_why || {})[i] || '')}</dd>`).join('')}</dl></section>` : ''}
        <div style="display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:14px 26px;margin-top:10px">
          <button class="tbtn" id="bEdit">Изменить замысел</button>
          <button class="tbtn" id="bReGen">Перегенерировать</button>
          <button class="make small" id="bStart"><span class="wax" aria-hidden="true" style="width:48px;height:46px"><svg width="22" height="24" viewBox="0 0 22 26" style="color:rgba(0,0,0,.5)"><use href="#quill"/></svg></span><span class="mk">Начать историю</span></button>
        </div>
        ${d._stats ? `<p class="sub" style="text-align:center;margin:0">${d._stats.predicted_n ?? '?'} токенов за ${d._stats.wall_s ?? '?'} с</p>` : ''}
      </div></section></div>`;
  $('#preview').querySelectorAll('[data-secret]').forEach(dd => dd.querySelector('button').onclick = () => {
    dd.innerHTML = `<span style="font-style:italic;color:var(--rubric)">${esc(d.npcs[dd.dataset.secret].secrets)}</span>`;
  });
  $('#bEdit').onclick = () => { draft = null; $('#preview').hidden = true; $('#newForm').hidden = false; setTitle('Новая летопись', 'сотворение мира'); };
  $('#bReGen').onclick = generate;
  $('#bStart').onclick = startStory;
}
async function startStory() {
  const b = $('#bStart'); b.disabled = true;
  const d = draft, p = d.player || {};
  try {
    const j = await api('api/scenes', { method: 'POST', body: JSON.stringify({
      title: d.title, setting: d.setting, rule_mode: d._mode, premise: d.premise || '', world: d.world || {},
      player: { name: p.name, description: p.description || '', personality: p.personality || '', speech: p.speech || '',
                kind: p.kind || '', attrs: p.attrs || {}, abilities: p.abilities || [], inventory: p.inventory || [] },
      npcs: (d.npcs || []).map(n => ({ name: n.name, description: n.description || '', personality: n.personality || '', speech: n.speech || '',
                                      secrets: n.secrets || '', kind: n.kind || '', abilities: n.abilities || [], present: n.present !== false })),
    }) });
    draft = null; $('#preview').hidden = true; $('#newForm').hidden = false;
    location.hash = '#/s/' + encodeURIComponent(j.id);
  } catch (e) { toast('Не удалось начать: ' + e.message); b.disabled = false; }
}

/* ============ game: connection ============ */
let ws = null, wsWanted = null, retry = 0, retryT = null;
let scene = null, st = null, busy = false, segs = {}, turnEl = null, writingEl = null, freshTurn = false, lastSent = '';
const story = $('#story'), feed = $('#feed'), inp = $('#inp');
const inputHist = LS.get('history', []); let histIdx = -1;

async function openScene(id) {
  setView('vGame');
  if (wsWanted !== id) closeSocket();
  story.innerHTML = `<p class="writing" style="justify-content:center;margin-top:4rem">${QUILL('var(--ink)', 26, 30).replace('<svg', '<svg class="quill"')}Открываю книгу<span class="dot1">.</span><span class="dot2">.</span><span class="dot3">.</span></p>`;
  try { renderScene(await api('api/scenes/' + encodeURIComponent(id))); }
  catch (e) { story.innerHTML = `<div class="story-head" style="margin-top:4rem"><h1>Летопись не найдена</h1><p>${esc(e.message)}</p></div>`; return; }
  connect(id);
  if (matchMedia('(hover: hover)').matches) inp.focus();
}
function connect(id) {
  wsWanted = id; clearTimeout(retryT);
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const base = location.pathname.replace(/[^/]*$/, '');
  const sock = new WebSocket(`${proto}://${location.host}${base}ws/${encodeURIComponent(id)}`);
  ws = sock;
  sock.onopen = () => {
    if (retry > 0) api('api/scenes/' + encodeURIComponent(id)).then(renderScene).catch(() => {});
    retry = 0; $('#netBanner').hidden = true;
  };
  sock.onmessage = ev => { if (ws === sock) onMsg(JSON.parse(ev.data)); };
  sock.onclose = () => {
    if (ws !== sock) return;
    ws = null;
    if (wsWanted !== id) return;
    $('#netBanner').hidden = false; retry++;
    retryT = setTimeout(() => wsWanted === id && connect(id), Math.min(10000, 600 * 2 ** Math.min(retry, 4)));
  };
}
function closeSocket() {
  wsWanted = null; clearTimeout(retryT);
  if (ws) { const s = ws; ws = null; try { s.close(); } catch {} }
  setBusy(false);
}
function wsSend(obj) {
  if (!ws || ws.readyState !== 1) { toast('Нет связи с сервером — подождите секунду.'); return false; }
  ws.send(JSON.stringify(obj)); return true;
}

/* ============ game: rendering ============ */
let stick = true;
feed.addEventListener('scroll', () => { stick = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 160; $('#toBottom').hidden = stick; }, { passive: true });
function scrollEnd(force) { if (force || stick) { feed.scrollTop = feed.scrollHeight; $('#toBottom').hidden = true; } }
$('#toBottom').onclick = () => feed.scrollTo({ top: feed.scrollHeight, behavior: 'smooth' });

function renderScene(s) {
  scene = s; segs = {}; turnEl = null; writingEl = null; freshTurn = false;
  document.title = s.title + ' · Narrator RPG';
  story.innerHTML = `<header class="story-head"><h1>${esc(s.title)}</h1><p>летопись · ${s.rule_mode === 'd20' ? 'жребий d20' : 'Система'}</p>
      <svg class="orn-line" aria-hidden="true" width="180" height="16"><use href="#orn"/></svg></header>
    ${s.setting ? `<div class="prol closed" id="prol">${prologueHTML(s.setting)}</div><div class="prol-btn"><button class="tbtn" id="prolBtn" aria-expanded="false">развернуть пролог</button></div>` : ''}`;
  const pb = $('#prolBtn');
  if (pb) pb.onclick = () => { const open = $('#prol').classList.toggle('closed') === false; pb.textContent = open ? 'свернуть пролог' : 'развернуть пролог'; pb.setAttribute('aria-expanded', open); };
  const log = s.log || [], rf = s.rewind_from;
  if (s.state && s.state.archived_turn > 1) addLine('e-mem', `— Архив: ходы I–${roman(s.state.archived_turn - 1)} сведены в летопись —`);
  let last = -1;
  for (let i = 0; i < log.length; i++) {
    const e = log[i];
    if (e.turn > 0 && e.turn !== last) {
      last = e.turn;
      let pl = null;
      for (let j = i; j < log.length && log[j].turn === e.turn; j++) if (log[j].kind === 'player') { pl = log[j]; break; }
      startTurn(e.turn, pl ? pl.text : null, rf != null && e.turn >= rf, false);
    }
    if (e.kind !== 'player') addSeg(e.kind, e.text, e.who, false);
  }
  if (!log.length) addLine('e-mem', '— Страницы ещё чисты: опиши первое действие героя —');
  renderSheet(s.state); updateTitle(s.state);
  stick = true; requestAnimationFrame(() => scrollEnd(true));
}
function container() { return turnEl || story; }
function addLine(cls, text) { const p = document.createElement('p'); p.className = cls; p.textContent = text; container().appendChild(p); return p; }
function playerHTML(text) {
  // *действие* plain, «речь» kept, (мысли) dimmed — the same markup the server parses
  return esc(text).replace(/\(([^)]+)\)/g, '<span class="th">($1)</span>').replace(/\*([^*]+)\*/g, '<span class="act">$1</span>');
}
function startTurn(n, text, rewindable = true, animate = true) {
  removeWriting();
  const t = document.createElement('div'); t.dataset.turn = n;
  if (animate) t.className = 'ink-in';
  t.innerHTML = `<div class="turn"><span class="ln"></span><svg class="dia" aria-hidden="true"><use href="#diamond"/></svg><span class="lb">ХОД ${roman(n)}</span><svg class="dia" aria-hidden="true"><use href="#diamond"/></svg><span class="ln"></span>${rewindable ? '<button class="rb">↶ откатить к этому ходу</button>' : ''}</div>
    ${text == null ? `<div class="e-player e-world">${QUILL()}<span class="h">Мир продолжает жить…</span></div>`
                   : `<div class="e-player">${QUILL()}<span class="h">${playerHTML(text)}</span></div>`}`;
  const rb = t.querySelector('.rb');
  if (rb) rb.onclick = async () => {
    if (busy) return;
    if (await ask({ title: `Откатить к ходу ${roman(n)}?`, text: 'Всё, что случилось после, будет вычеркнуто из летописи. Это нельзя отменить.', ok: 'Откатить' }))
      if (wsSend({ type: 'rewind', turn: n })) setBusy(true);
  };
  story.appendChild(t); turnEl = t; freshTurn = true;
  return t;
}

const LEAD = /^(?:🎲|⚙️|⚙|⚔️|⚔|👁|🚪|🧠|🎬)\s*/u;
function checkHTML(text) {
  const t = String(text).replace(LEAD, '');
  const m = t.match(/d20\((\d+)\)/) || t.match(/(\d+)\s+против/) || t.match(/→\s*(\d+)/) || t.match(/=\s*(\d+)/);
  const ok = /успех|попадани/i.test(t), fail = /провал|промах|неудач/i.test(t);
  const cls = ok && !fail ? 'ok' : fail ? 'fail' : 'neu';
  const i = t.indexOf(' — ');
  let body = i > 0 ? `<b>${esc(t.slice(0, i))}</b> — ${esc(t.slice(i + 3))}` : esc(t);
  body = body.replace(/(успех|попадание!?|критический успех!?)/i, '<span class="r-ok">$1</span>').replace(/(провал|промах!?)/i, '<span class="r-fail">$1</span>');
  return { cls, num: m ? m[1] : '—', body };
}
function noteHTML(kind, text, who) {
  let t = String(text).replace(LEAD, ''), title = who === 'Директор' ? 'Директор' : 'Система';
  const h = t.match(/^#{2,4}\s*(.+)\n?/);
  if (h) { title = h[1].replace(/[⟦⟧]/g, '').trim(); t = t.slice(h[0].length); }
  if (/ДИРЕКТОР/i.test(title)) title = 'Директор';
  t = t.replace(/^🎬\s*/m, '');
  const icon = /Директор/.test(title)
    ? '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16"><path d="M8 1 V 15 M2 5 L 14 11 M14 5 L 2 11" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>'
    : '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M8 4 V 8 L 11 10" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';
  const co = p => `<svg class="co" aria-hidden="true" style="${p}"><use href="#diamond"/></svg>`;
  return `${co('top:-7px;left:-7px')}${co('bottom:-7px;right:-7px')}<div class="nt">${icon}${esc(title)}</div><div class="nb">${esc(t.trim()).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')}</div>`;
}
function fillSeg(el, kind, text, who, streaming = false) {
  if (kind === 'narration') {
    el.className = 'narr'; el.textContent = text;
    if (el._ni) el.classList.add('ni');
    if (streaming) el.insertAdjacentHTML('beforeend', '<span class="blot" aria-hidden="true"></span>');
  } else if (kind === 'speech') {
    el.className = 'e-npc';
    el.innerHTML = `<span class="npc" style="color:${inkOf(who)}">${esc(who)}.</span> — ${esc(String(text).trim())}${streaming ? '<span class="blot" aria-hidden="true"></span>' : ''}`;
  } else if (kind === 'roll') {
    const c = checkHTML(text);
    el.className = 'e-check';
    el.innerHTML = `<span class="seal ${c.cls}${el._anim ? ' stamp-in' : ''}" aria-hidden="true">${esc(c.num)}</span><span class="t">${c.body}</span>`;
  } else if (kind === 'mem') {
    el.className = 'e-mem'; el.textContent = '— ' + String(text).replace(/^🧠\s*/u, '') + ' —';
  } else if (kind === 'error') {
    el.className = 'e-err';
    el.innerHTML = `<svg aria-hidden="true" width="18" height="16"><use href="#blotico"/></svg><span>Перо сломалось: ${esc(text)}</span>${lastSent ? '<button>Попробовать снова</button>' : ''}`;
    const b = el.querySelector('button'); if (b) b.onclick = () => { b.remove(); send(lastSent); };
  } else {
    el.className = 'e-note'; el.innerHTML = noteHTML(kind, text, who);
  }
}
function addSeg(kind, text, who, animate = true) {
  const el = document.createElement(kind === 'narration' ? 'p' : 'div');
  if (kind === 'narration' && freshTurn) { el._ni = true; freshTurn = false; }
  el._anim = animate;
  fillSeg(el, kind, text, who);
  if (animate) el.classList.add('ink-in');
  container().appendChild(el);
  return el;
}
function showWriting() {
  removeWriting();
  writingEl = document.createElement('p'); writingEl.className = 'writing';
  writingEl.innerHTML = `${QUILL('var(--ink)', 26, 30).replace('<svg', '<svg class="quill"')}Перо скрипит<span class="dot1">.</span><span class="dot2">.</span><span class="dot3">.</span>`;
  container().appendChild(writingEl); scrollEnd();
}
function removeWriting() { if (writingEl) { writingEl.remove(); writingEl = null; } }

/* ============ game: protocol ============ */
function onMsg(m) {
  switch (m.type) {
    case 'hello': renderSheet(m.state); updateTitle(m.state); setBusy(!!m.busy); return;
    case 'player':
      if (m.turn) startTurn(m.turn, m.text);
      else { const d = document.createElement('div'); d.className = 'e-player e-cmd ink-in'; d.innerHTML = `<span class="tag">команда</span><span class="h">${esc(m.text)}</span>`; container().appendChild(d); }
      if (busy) showWriting(); scrollEnd(true); return;
    case 'continue': startTurn(m.turn, null); if (busy) showWriting(); scrollEnd(true); return;
    case 'reload':
      if (m.scene) renderScene(m.scene); else if (scene) api('api/scenes/' + encodeURIComponent(scene.id)).then(renderScene);
      setBusy(!!m.busy); return;
    case 'memory': addSeg('mem', m.text); if (m.state) renderSheet(m.state); scrollEnd(); return;
    case 'chunk': {
      let el = segs[m.id];
      if (!el) {
        removeWriting();
        el = segs[m.id] = document.createElement(m.kind === 'narration' ? 'p' : 'div'); el._t = '';
        if (m.kind === 'narration' && freshTurn) { el._ni = true; freshTurn = false; }
        container().appendChild(el);
      }
      el._t += m.delta; fillSeg(el, m.kind, el._t, m.who, true);
      scrollEnd(); return;
    }
    case 'retract': segs[m.id]?.remove(); delete segs[m.id]; return;
    case 'replace': {
      const el = segs[m.id]; if (!el) return;
      let ni = el._ni;
      for (const s of m.segments) {
        const n = document.createElement(s.kind === 'narration' ? 'p' : 'div');
        if (s.kind === 'narration' && ni) { n._ni = true; ni = false; }
        fillSeg(n, s.kind, s.text, s.who); el.before(n);
      }
      el.remove(); delete segs[m.id];
      if (busy && !Object.keys(segs).length) showWriting();
      scrollEnd(); return;
    }
    case 'segment': removeWriting(); addSeg(m.kind, m.text, m.who); if (busy) showWriting(); scrollEnd(); return;
    case 'director_msg': {
      const dl = $('#dirLog');
      if (dl) {
        const empty = dl.querySelector('li.sub'); if (empty) empty.remove();
        const who = m.who || 'Директор';
        const line = (who === 'Игрок' ? 'Игрок: ' : 'Директор: ') + String(m.text || '');
        const cls = who === 'Игрок' ? 'dir-you' : 'dir-me';
        dl.insertAdjacentHTML('afterbegin', `<li class="${cls}">${esc(line)}</li>`);
      }
      selectTab('director');
      return;
    }
    case 'turn_end': {
      removeWriting(); lastSent = '';
      renderSheet(m.state); updateTitle(m.state);
      const s = m.stats || {};
      if (s.llm_calls && s.llm_calls.length) {
        const cache = s.prompt_n ? Math.round(100 * (s.cache_n || 0) / ((s.cache_n || 0) + s.prompt_n)) : null;
        addLine('meta', [`ход ${roman(s.turn)}`, `${String(s.wall_s).replace('.', ',')} с`,
          `${s.llm_calls.length} ${plural(s.llm_calls.length, ['вызов', 'вызова', 'вызовов'])} ИИ`, cache != null ? `кэш ${cache} %` : ''].filter(Boolean).join(' · '));
      }
      setBusy(false); scrollEnd(); return;
    }
    case 'error':
      removeWriting(); addSeg('error', m.text, null); setBusy(false); scrollEnd(true);
      if (lastSent && !inp.value) { inp.value = lastSent; autosize(); }   // the turn was rolled back — keep the text
      return;
  }
}
function setBusy(b) {
  busy = b;
  document.body.classList.toggle('busy', b);
  ['#bSend', '#bCont', '#bRegen', '#bSys', '#bDir', '#bAudit'].forEach(s => { $(s).disabled = b; });
  $$('.turn .rb').forEach(x => { x.disabled = b; });
  $('#sendLbl').textContent = b ? 'Пишет…' : 'Записать';
  if (!b) removeWriting();
}
function updateTitle(s) { if (s && scene) setTitle(scene.title, `${s.rule_mode === 'd20' ? 'd20' : 'Система'} · ход ${roman(s.turn) || 0}`); }

/* ============ game: actions ============ */
/* the player writes freely (*действие или мысли*, «речь» / "речь", (мысли)); the server and the narrator read it */
const PLACEHOLDER = 'Что вы делаете, говорите или думаете?..';
function paintModes() { if (!inp.dataset.advice) inp.placeholder = PLACEHOLDER; }
function markInput(t) {
  if (/^(система|system|\/)/i.test(t)) return t;                  // commands go as typed
  if (inp.dataset.advice) return /[*«»"„“()]/.test(t) ? t : `«${t}»`;   // a companion's turn: the words are advice
  return t;
}
paintModes();

function send(text) {
  const t = text != null ? text.trim() : markInput(inp.value.trim());
  if (!t || busy) return;
  if (!wsSend({ type: 'input', text: t })) return;
  if (text == null) { inp.value = ''; autosize(); }
  if (inputHist[inputHist.length - 1] !== t) { inputHist.push(t); if (inputHist.length > 50) inputHist.shift(); LS.set('history', inputHist); }
  lastSent = t; histIdx = -1; stick = true; setBusy(true); showWriting();
}
$('#bSend').onclick = () => send();
$('#bCont').onclick = () => { if (!busy && wsSend({ type: 'continue' })) { stick = true; setBusy(true); } };
$('#bRegen').onclick = async () => {
  if (busy) return;
  if (await ask({ title: 'Переписать последний ход?', text: 'Рассказчик заново напишет ответ на последнее действие героя.', ok: 'Переписать' }))
    if (wsSend({ type: 'regenerate' })) { stick = true; setBusy(true); }
};
function autosize() { inp.style.height = 'auto'; inp.style.height = Math.min(inp.scrollHeight + 2, innerHeight * .34) + 'px'; }
inp.addEventListener('input', autosize);
inp.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  else if (e.key === 'ArrowUp' && (!inp.value || histIdx >= 0) && inputHist.length) {
    e.preventDefault(); histIdx = histIdx < 0 ? inputHist.length - 1 : Math.max(0, histIdx - 1); inp.value = inputHist[histIdx]; autosize();
  } else if (e.key === 'ArrowDown' && histIdx >= 0) {
    e.preventDefault(); histIdx++; if (histIdx >= inputHist.length) { histIdx = -1; inp.value = ''; } else inp.value = inputHist[histIdx]; autosize();
  }
});
function prefill(text) { inp.value = text; autosize(); closeSheet(); inp.focus(); inp.setSelectionRange(text.length, text.length); }

const CMDS = [['Статус', 'здоровье, мана', 'статус'], ['Опись', 'инвентарь', 'инвентарь'], ['Способности', '', 'способности'], ['Состояния', '', 'состояния'],
              ['Бой', 'порядок ходов', 'бой'], ['Книга путей', 'пути, отрасли, следы', 'пути'],
              ['Спросить Систему…', 'любой вопрос о правилах', null], ['Справка', '', 'помощь']];
const menu = $('#sysMenu');
menu.innerHTML = CMDS.map(([l, k, c], i) => `<button role="menuitem" data-i="${i}"><span>${l}</span><span class="k">${k}</span></button>`).join('');
function toggleMenu(open) { menu.hidden = !open; $('#bSys').setAttribute('aria-expanded', open); }
$('#bSys').onclick = e => { e.stopPropagation(); toggleMenu(menu.hidden); };
document.addEventListener('click', e => { if (!menu.hidden && !menu.contains(e.target)) toggleMenu(false); });
menu.onclick = async e => {
  const b = e.target.closest('[data-i]'); if (!b) return;
  toggleMenu(false);
  const c = CMDS[b.dataset.i][2];
  if (c) send('Система, ' + c);
  else { const v = await ask({ title: 'Обратиться к Системе', text: 'Вопрос или команда: «что нужно для пути огня?», «осмотри Сильфу», «распредели Сила +1»', ok: 'Спросить', cancel: 'Отмена', input: '' }); if (v && v.trim()) send('Система, ' + v.trim()); }
};

function sendDirector(text, audit) {
  const t = (text || '').trim();
  if (!audit && !t) { $('#dirInp').focus(); return toast('Напишите Директору — совет, правку или просто вопрос', true, 2800); }
  if (busy) return;
  if (!wsSend({ type: audit ? 'director_audit' : 'director', text: t })) return;
  if ($('#dirInp')) $('#dirInp').value = '';
  // stay on Director pane — separate chat, no story auto-replies
  selectTab('director');
  setBusy(true);
  // optimistic player line in переписка
  const dl = $('#dirLog');
  if (dl && t) {
    const empty = dl.querySelector('li.sub'); if (empty) empty.remove();
    dl.insertAdjacentHTML('afterbegin', `<li class="dir-you">${esc('Игрок: ' + t)}</li>`);
  }
}
$('#bDir').onclick = () => sendDirector($('#dirInp').value, false);
$('#dirInp') && ($('#dirInp').onkeydown = e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#bDir').click(); }
});
$('#bAudit').onclick = () => sendDirector('проверь карточки', true);

let setT = null;
function saveSettings(patch) {
  if (!scene) return;
  clearTimeout(setT);
  setT = setTimeout(() => api('api/scenes/' + encodeURIComponent(scene.id) + '/settings', { method: 'POST', body: JSON.stringify(patch) })
    .then(s => { if (st) st.settings = s; }).catch(e => toast('Настройки не сохранились: ' + e.message)), 250);
}
$('#sMult').oninput = e => { $('#sMultV').textContent = '×' + e.target.value; saveSettings({ length_mult: +e.target.value }); };
$('#sMax').onchange = e => saveSettings({ max_speaks: e.target.value === '' ? null : +e.target.value });
$('#sLeave').onchange = e => saveSettings({ inactive_leave: e.target.value === '' ? null : +e.target.value });

/* ============ hero sheet ============ */
function openSheet() { $('#sheet').classList.add('open'); $('#backdrop').classList.add('on'); }
function closeSheet() { $('#sheet').classList.remove('open'); $('#backdrop').classList.remove('on'); }
function selectTab(t) {
  $$('.rib').forEach(b => b.setAttribute('aria-pressed', b.dataset.tab === t));
  $$('.pane').forEach(p => p.classList.toggle('on', p.dataset.pane === t));
  LS.set('tab', t);
}
$$('.rib').forEach(b => b.onclick = () => selectTab(b.dataset.tab));
$('#bSheetClose').onclick = closeSheet; $('#backdrop').onclick = closeSheet; $('#vitals').onclick = openSheet;
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeSheet(); toggleMenu(false); } });
selectTab(LS.get('tab', 'hero'));

const gauge = (label, c, m, cls) => `<div class="gauge"><div class="row"><span>${label}</span><b>${c ?? '?'} / ${m ?? '?'}</b></div><div class="bar ${cls}"><span style="width:${pct(c, m)}%"></span></div></div>`;
const COND_ICON = {
  'кровотечение': '<svg aria-hidden="true" width="14" height="18" viewBox="0 0 14 18"><path d="M7 1 C 10 6, 13 9, 13 12 A 6 6 0 0 1 1 12 C 1 9, 4 6, 7 1 Z" fill="none" stroke="var(--rubric)" stroke-width="1.3"/></svg>',
};
const condIco = n => COND_ICON[n] || '<svg aria-hidden="true" width="18" height="12" viewBox="0 0 18 12"><path d="M1 6 C 4 1, 14 1, 17 6 C 14 11, 4 11, 1 6 Z" fill="none" stroke="var(--ink)" stroke-width="1.2"/><circle cx="9" cy="6" r="2.2" fill="var(--ink)"/></svg>';
const turnsLeft = n => `ещё ${n} ${plural(n, ['ход', 'хода', 'ходов'])}`;

let sheetWho = null;   // whose sheet the «Герой» ribbon shows: the hero or a companion
const firstName = n => String(n || '').split(/\s+/)[0];
const ROMAN = ['', 'I', 'II', 'III', 'IV', 'V'];
const pts = n => `${n} ${plural(n, ['очко', 'очка', 'очков'])}`;

/* ============ Книга путей ============ */
function bookPath(book, p, c, whom) {
  const s = (book.status[c.id] || {})[p.id] || {};
  const have = s.have || 0, n = p.tiers.length, word = p.core ? 'ранг' : 'ступень';
  const pips = p.tiers.map(t => `<i class="${t.tier <= have ? 'on' : ''}"></i>`).join('');
  const roots = p.parents.length ? `<span class="kd">от: ${p.parents.map(x => esc(x.name) + ' ' + ROMAN[x.rank]).join(', ')}</span>` : '';
  const tier = t => {
    const got = t.tier <= have, next = t.tier === have + 1;
    const abil = t.abilities.map(a => `<div class="bab"><span class="nm">${a.kind === 'active' ? '⚡' : '◇'} ${esc(a.name)}</span>${a.stats ? `<span class="kd">${esc(a.stats)}</span>` : ''}${a.description ? `<span class="ds">${esc(a.description)}</span>` : ''}</div>`).join('');
    let foot = '';
    if (next && s.ok) foot = `<button class="tbtn" data-cmd="открой ${word} ${esc(whom)}${esc(p.name)}">Открыть ${word} ${ROMAN[t.tier]} · ${pts(t.cost)}</button>`;
    else if (next) foot = `<span class="ds miss">Не хватает: ${(s.missing || []).map(esc).join('; ')}</span>`;
    else if (!got) foot = `<span class="ds miss">${pts(t.cost)}${t.story ? ' · ' + esc(t.story) : ''}</span>`;
    return `<li class="${got ? 'got' : next ? 'next' : ''}"><b class="rn">${ROMAN[t.tier]}</b><div>${abil}${foot}</div></li>`;
  };
  return `<details class="bpath${s.ok ? ' ready' : ''}"${have ? ' open' : ''}>
    <summary><span class="top"><span class="nm">${esc(p.name)}</span><span class="pips" title="${word} ${ROMAN[have] || '—'} из ${ROMAN[n]}">${pips}</span></span>
      ${roots}${p.description ? `<span class="ds">${esc(p.description)}</span>` : ''}
      ${!have && s.tier ? `<span class="kd">${word} I: ${esc(p.tiers[0].abilities.map(a => a.name).join(', '))} · ${pts(p.tiers[0].cost)}${s.ok ? '' : ' · закрыто'}</span>` : ''}</summary>
    <ol class="tiers">${p.tiers.map(tier).join('')}</ol></details>`;
}

function bookPane(state, sw) {
  const book = state.book; if (!book) return '';
  const c = state.characters[sheetWho] || state.characters[state.player_id];
  const hero = c.id === state.player_id, whom = hero ? '' : firstName(c.name) + ' ';
  const core = book.paths.filter(p => p.core), br = book.paths.filter(p => !p.core);
  const leads = book.leads || [];
  const n = book.points[c.id] || 0;
  return sw + `
    <div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px">
      <span style="font:700 28px/1.1 var(--f-head)">Книга путей</span>
      <span style="font:700 16px/1.3 var(--f-sc);letter-spacing:.08em;color:${n ? 'var(--rubric)' : 'var(--ink-soft)'}">${esc(firstName(c.name))} · ${pts(n)}</span>
    </div>
    <p class="sub" style="margin:0">Очки способностей приходят с уровнем. Атрибуты не открывают пути — они усиливают то, что ты умеешь.</p>
    <section><h2 class="sh">Основные пути</h2><div class="bpaths">${core.map(p => bookPath(book, p, c, whom)).join('')}</div></section>
    <section><h2 class="sh">Отрасли</h2>${br.length ? `<div class="bpaths">${br.map(p => bookPath(book, p, c, whom)).join('')}</div>`
      : '<p class="sub" style="margin:0">Пока ни одной. Увидь в мире необычное искусство — заклинание, приём, обряд — и изучи его: у книги, наставника или долгим наблюдением.</p>'}</section>
    ${leads.length ? `<section><h2 class="sh">Следы</h2><ul class="leads">${leads.map(l => `<li><span class="nm">${esc(l.what)}</span>${l.source ? `<span class="kd">${esc(l.source)}</span>` : ''}
      <span class="ds">${l.studied ? `${QUILL('var(--ink)', 16, 18)} изучено — Летописец пишет отрасль…` : esc(l.hint || 'Узнай больше: книга, наставник, долгое наблюдение.')}</span></li>`).join('')}</ul></section>` : ''}
    <button class="tbtn" data-pre="Система, ">Спросить Систему…</button>`;
}

const EFF = { damage: 'урон', heal: 'лечение', shield: 'щит', buff: 'усиление', debuff: 'ослабление', control: 'оглушение' };
const BON = { hp: 'макс. HP', mp: 'макс. MP', ac: 'КБ', melee: 'урон вблизи', ranged: 'урон издали', spell: 'сила чар', heal: 'сила лечения', init: 'инициатива' };
function abilStats(a) {
  if (a.kind === 'passive') return Object.entries(a.bonus || {}).filter(([, v]) => v).map(([k, v]) => `${BON[k] || k} +${v}`).join(', ');
  const e = EFF[a.effect];
  const pw = !e ? '' : a.effect === 'control' ? `${e} ${a.power} ход.` : (a.effect === 'buff' || a.effect === 'debuff') ? `${e} ${a.power}` : `${e} ${a.power}`;
  return [pw && pw + (a.scale ? ' +' + a.scale : ''), a.mp ? a.mp + ' MP' : ''].filter(Boolean).join(' · ');
}
function memberSheet(state, c, info) {
  const d20 = state.rule_mode === 'd20', hero = c.id === state.player_id, fn = firstName(c.name);
  const whom = hero ? '' : fn + ' ';
  const abs = c.abilities || [];
  const abil = a => {
    const kd = [a.kind === 'active' ? 'активная' : 'пассивная', abilStats(a) || a.cost, a.cooldown && 'перезарядка ' + a.cooldown].filter(Boolean).join(' · ');
    const use = a.kind === 'active' ? `<button class="tbtn" data-pre="${hero ? `Применяю «${esc(a.name)}»: ` : `${esc(fn)}, используй «${esc(a.name)}»: `}">${hero ? 'Применить' : 'Приказать'}</button>` : '';
    return `<div class="abil"><div class="top"><span class="nm">${esc(a.name)}</span>${use}</div>
      <span class="kd">${esc(kd)}</span>${a.description ? `<span class="ds">${esc(a.description)}</span>` : ''}</div>`;
  };
  const item = i => {
    const eff = ITEM_EFFECT[i.effect];
    const sub = [i.equipped && 'надето', i.damage && 'урон ' + i.damage, i.armor && 'броня +' + i.armor,
                 eff && eff + POWER_WORD[i.power || 2], !eff && !i.damage && !i.armor && i.kind !== 'предмет' && i.kind].filter(Boolean).join(' · ');
    const wearable = i.damage || i.armor || i.slot;
    let btn = '';
    if (hero && wearable) btn = `<button class="tbtn" data-cmd="${i.equipped ? 'сними' : 'экипируй'} ${esc(i.name)}">${i.equipped ? 'Снять' : 'Надеть'}</button>`;
    else if (hero && i.effect === 'damage') btn = `<button class="tbtn" data-pre="Система, используй ${esc(i.name)} на ">Бросить…</button>`;
    else if (hero && i.effect) btn = `<button class="tbtn" data-cmd="используй ${esc(i.name)}">Использовать</button>`;
    return `<div><span style="font-weight:600">${esc(i.name)}</span>${sub ? `<br><span class="sub">${esc(sub)}</span>` : ''}</div>
      <div style="color:var(--ink-soft)">×${i.qty}</div><div>${btn}</div>`;
  };
  const cond = c.conditions || [];
  return `
    <div style="display:flex;align-items:center;gap:16px">
      <span class="oval" aria-hidden="true" style="color:${hero ? 'var(--rubric-hi)' : inkOf(c.name)}">${esc(initial(c.name))}</span>
      <div style="display:flex;flex-direction:column;min-width:0">
        <span style="font:700 30px/1.1 var(--f-head)">${esc(c.name)}</span>
        ${c.kind ? `<span style="font-size:17px;font-style:italic;color:var(--ink-soft)">${esc(c.kind)}</span>` : ''}
        <span style="font:700 16px/1.3 var(--f-sc);letter-spacing:.1em;color:var(--rubric)">уровень ${c.level}${hero ? '' : ' · спутник'}</span>
      </div>
    </div>
    ${info.filling ? `<p class="writing" style="margin:0;font-size:16px">${QUILL('var(--ink)', 22, 26).replace('<svg', '<svg class="quill"')}Летописец дописывает карточку<span class="dot1">.</span><span class="dot2">.</span><span class="dot3">.</span></p>` : ''}
    <div style="display:flex;flex-direction:column;gap:10px">${gauge('Здоровье', c.hp, c.max_hp, 'hp')}${gauge('Мана', c.mp, c.max_mp, 'mp')}${gauge('Опыт', c.exp, c.exp_next, 'xp')}
</div>
    ${!hero && c.description ? `<p style="margin:0;font-size:16px;line-height:1.5">${esc(c.description)}</p>` : ''}
    <section><h2 class="sh">Атрибуты</h2>${attrTable(c.attrs, d20)}
      ${c.attr_points ? `<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:10px;font-size:16px">
        <span>Свободные очки: <b style="font:700 19px/1 var(--f-head);color:var(--rubric)">${c.attr_points}</b></span>
        <button class="tbtn" data-pre="Система, распредели ${esc(whom)}">Распределить…</button></div>` : ''}</section>
    ${cond.length ? `<section><h2 class="sh">Состояния</h2><div class="conds">${cond.map(x => `<div>${condIco(x.name)}<span>${esc(x.name[0].toUpperCase() + x.name.slice(1))}</span><em>${turnsLeft(x.turns_left)}${x.potency ? ` · −${x.potency} HP` : ''}</em></div>`).join('')}</div></section>` : ''}
    <section><h2 class="sh">Способности</h2>${abs.length ? `<div style="display:flex;flex-direction:column;gap:12px">${abs.filter(a => a.kind === 'active').concat(abs.filter(a => a.kind !== 'active')).map(abil).join('')}</div>` : '<p class="sub" style="margin:0">Пока ни одной.</p>'}</section>
    <section><h2 class="sh">Пути</h2><p style="margin:0 0 6px;font-size:16px">${c.ability_points ? `Свободно: <b style="font:700 19px/1 var(--f-head);color:var(--rubric)">${pts(c.ability_points)}</b> способностей.` : 'Свободных очков способностей нет — они приходят с уровнем.'}</p>
      <button class="tbtn" data-tab-go="book">Открыть Книгу путей</button></section>
    <section><h2 class="sh">Опись${hero ? '' : ' <span class="sub" style="font-family:var(--f-body);letter-spacing:0">— снаряжением спутник распоряжается сам</span>'}</h2>${(c.inventory || []).length ? `<div class="inv">${c.inventory.map(item).join('')}</div>` : '<p class="sub" style="margin:0">Пусто.</p>'}</section>
    ${hero ? '' : `<div style="text-align:center"><button class="tbtn" data-dismiss="${esc(c.name)}">Отпустить из отряда</button></div>`}`;
}

const ITEM_EFFECT = { heal: 'лечение', mana: 'восстановление маны', cure: 'противоядие', damage: 'урон по цели', buff: 'усиление' };
const POWER_WORD = { 1: ' (слабое)', 2: '', 3: ' (сильное)', 4: ' (великое)' };

/* whose turn it is in a fight; on a companion's turn the input becomes advice */
const REACT_TIP = {
  dodge: 'Ловкость против удара: успех — мимо, провал — полный урон. Тяжёлая броня мешает.',
  block: 'Сила против удара: успех — урон вдвое меньше. Щит помогает.',
  parry: 'Трудно: успех — без урона и контратака, провал — урон ×1,5.',
  take: 'Обычный шанс промаха; броня вычитается из урона. Не надоедает врагу.',
};
function paintTurn(state) {
  const bar = $('#turnbar'), cb = state.combat || {};
  if (!cb.active || !(cb.order || []).length) { bar.hidden = true; inp.dataset.advice = ''; paintModes(); return; }
  const cur = state.characters[(cb.order[cb.current] || {}).id] || {};
  bar.hidden = false;
  const th = state.threat;
  if (th) {
    inp.dataset.advice = '';
    paintModes();
    bar.innerHTML = `<span><b style="color:var(--rubric-hi)">⚠ ${esc(th.foe)}</b> бросается на вас (${esc(th.weapon)}). Опишите, как защищаетесь, — или быстро:</span>
      <span class="reacts">${th.options.map(o => `<button class="tbtn" data-react="${esc(o.text)}" title="${esc(REACT_TIP[o.key] || '')}">${esc(o.label)} <b>${o.pct}%</b></button>`).join('')}</span>`;
    bar.querySelectorAll('[data-react]').forEach(b => b.onclick = () => send(b.dataset.react));
    inp.placeholder = 'Или опишите реакцию своими словами…';
    return;
  }
  if (cb.awaiting) {
    const c = state.characters[cb.awaiting] || {};
    bar.innerHTML = `<span><b style="color:${inkOf(c.name)}">Ход ${esc(c.name)}</b> · раунд ${cb.round}. Посоветуйте, что сделать, — или пусть решает сама.</span>
      <button class="tbtn" id="bSelfDecide">Пусть решает сама</button>`;
    inp.dataset.advice = c.name;
    inp.placeholder = `Совет для ${c.name}…`;
    $('#bSelfDecide').onclick = () => { if (!busy && wsSend({ type: 'continue' })) { stick = true; setBusy(true); } };
  } else {
    inp.dataset.advice = '';
    paintModes();
    bar.innerHTML = cur.is_player ? `<span><b>Ваш ход</b> · раунд ${cb.round}</span>`
                                  : `<span>Ход: ${esc(cur.name || '?')} · раунд ${cb.round}</span>`;
  }
}

function renderSheet(state) {
  if (!state) return;
  st = state;
  paintTurn(state);
  const p = state.characters[state.player_id]; if (!p) return;
  const d20 = state.rule_mode === 'd20';
  $('#vitals').innerHTML = `${esc(p.hp)} / ${esc(p.max_hp)}<span class="mini"><i style="width:${pct(p.hp, p.max_hp)}%"></i></span>`;

  // ---- отряд: переключатель листов ----
  const party = (state.party || [state.player_id]).filter(id => state.characters[id]);
  if (!party.includes(sheetWho)) sheetWho = state.player_id;
  const who = state.characters[sheetWho];
  const sw = party.length > 1 ? `<nav class="party-sw" aria-label="Члены отряда">${party.map(id => {
      const c = state.characters[id];
      return `<button data-who="${esc(id)}" aria-pressed="${id === sheetWho}" title="${esc(c.name)}"><span class="oval sm" aria-hidden="true" style="color:${id === state.player_id ? 'var(--rubric-hi)' : inkOf(c.name)}">${esc(initial(c.name))}</span><span>${esc(firstName(c.name))}</span>${c.attr_points || c.ability_points ? '<i class="dotp" title="есть свободные очки"></i>' : ''}</button>`;
    }).join('')}<span class="sub">${party.length} / ${state.party_max || 4}</span></nav>` : '';
  $('#paneHero').innerHTML = sw + memberSheet(state, who, (state.party_info || {})[sheetWho] || {});
  $('#paneBook').innerHTML = bookPane(state, sw);
  const bp = (state.book && state.book.points) || {};
  $('#bookN').textContent = party.some(id => bp[id]) ? '•' : '';

  // ---- сцена: отряд, противники, остальные ----
  const present = (state.present || []).filter(i => i !== state.player_id && state.characters[i]);
  const companions = party.filter(i => i !== state.player_id);
  const foes = present.filter(i => state.characters[i].side === 'hostile');
  const others = present.filter(i => !companions.includes(i) && !foes.includes(i));
  $('#presentN').textContent = present.length || '';
  const full = party.length >= (state.party_max || 4);
  const ATTR_SHORT = { 'Сила': 'Сил', 'Ловкость': 'Лов', 'Выносливость': 'Вын', 'Воля': 'Вол', 'Разум': 'Раз', 'Влияние': 'Вли' };
  const near = (id, kind) => {
    const c = state.characters[id] || {}, rv = c.reveal || {}, known = rv.attrs && c.attrs && Object.keys(c.attrs).length;
    const seen = rv.kind && rv.attrs && rv.abilities;
    const down = c.hp === 0;
    const acts = [];
    if (!seen) acts.push(`<button class="tbtn" data-cmd="осмотри ${esc(c.name)}">Осмотр</button>`);
    if (kind === 'other' && !down) acts.push(full ? '<span class="sub">отряд полон</span>' : `<button class="tbtn" data-pre="«${esc(c.name)}, пойдёшь со мной? Предлагаю присоединиться к моему отряду.»">Позвать в отряд</button>`);
    return `<div class="near${down ? ' down' : ''}"><span class="oval sm" aria-hidden="true" style="color:${inkOf(c.name)}">${esc(initial(c.name))}</span>
      <div class="bd"><div class="hd"><span class="npc" style="color:${inkOf(c.name)}">${esc(c.name || '?')}</span>${down ? '<span class="note">повержен(а)</span>' : seen && kind !== 'party' ? '<span class="note">осмотрен(а)</span>' : ''}</div>
      ${rv.kind ? `<span style="font-size:15px;font-style:italic;color:var(--ink-soft)">${esc(c.kind || 'вид неизвестен')}</span>` : '<span style="font-size:15px;font-style:italic;color:var(--ink-soft)">вид неизвестен · характеристики скрыты</span>'}
      ${c.hp != null ? `<div class="bar hp thin"><span style="width:${pct(c.hp, c.max_hp)}%"></span></div><span style="font-size:14px;color:var(--ink-soft)">здоровье ${c.hp} / ${c.max_hp}${kind === 'party' ? ` · ур. ${c.level}` : ''}</span>` : ''}
      ${known && kind !== 'party' ? `<span style="font-size:15px">${Object.entries(c.attrs).map(([k, v]) => `${ATTR_SHORT[k] || k} ${v}`).join(' · ')}</span>` : ''}
      ${rv.abilities && kind !== 'party' && (c.abilities || []).length ? `<span style="font-size:15px">${c.abilities.map(a => `${esc(a.name)} <i style="color:var(--ink-soft)">(${a.kind === 'active' ? 'акт.' : 'пасс.'})</i>`).join(' · ')}</span>` : ''}
      ${(c.conditions || []).length ? `<span style="font-size:14px;color:var(--rubric)">${c.conditions.map(x => esc(x.name) + ' · ' + x.turns_left).join(', ')}</span>` : ''}
      ${kind === 'party' ? `<div><button class="tbtn" data-who-open="${esc(id)}">Лист спутника</button></div>` : acts.length ? `<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center">${acts.join('')}</div>` : ''}
      </div></div>`;
  };
  const block = (title, ids, kind, empty) => `<section><h2 class="sh">${title}</h2><div style="display:flex;flex-direction:column;gap:16px">${ids.length ? ids.map(i => near(i, kind)).join('') : `<p class="sub" style="margin:0">${empty}</p>`}</div></section>`;
  const cb = state.combat || {};
  let h = '';
  if (companions.length) h += block(`Отряд · ${party.length} / ${state.party_max || 4}`, companions, 'party', '');
  if (foes.length || cb.active) h += block(`Противники${foes.length ? ' · ' + foes.filter(i => state.characters[i].hp !== 0).length : ''}`, foes, 'foe', 'Враги ещё не показались.');
  h += block('Рядом', others, 'other', companions.length || foes.length ? 'Больше никого.' : 'Никого поблизости.');
  if (cb.active) {
    h += `<section><h2 class="sh">Бой · раунд ${cb.round}</h2><ol class="init">${(cb.order || []).map((o, i) => {
      const c = state.characters[o.id] || {}, me = o.id === state.player_id;
      return `<li class="${i === cb.current ? 'cur' : ''}"><span class="who">${i === cb.current ? 'ходит' : ''}</span>
        ${me ? `<span style="font-weight:600">${esc(c.name)}</span>` : `<span class="npc" style="font-size:18px;color:${inkOf(c.name)}">${esc(c.name || o.id)}</span>`}
        <span class="r">иниц. ${o.initiative} · ${c.hp != null ? `${c.hp} / ${c.max_hp}` : '? / ?'}</span></li>`;
    }).join('')}</ol></section>`;
  }
  h += `<section><h2 class="sh">Память летописи</h2><p style="margin:0;font-size:17px;line-height:1.5">Сводок: ${state.summaries || 0} · фактов: ${state.facts || 0}<br>
    <span style="font-style:italic;color:var(--ink-soft)">${state.archived_turn > 1 ? `архив до хода ${roman(state.archived_turn - 1)}` : 'архив пока пуст'}</span></p></section>`;
  $('#paneScene').innerHTML = h;

  // ---- директор ----
  const dl = state.director_log || [];
  $('#dirLog').innerHTML = dl.length
    ? dl.slice().reverse().map(x => {
        const isDir = /^Директор:/i.test(x);
        const isPl = /^Игрок:/i.test(x);
        const cls = isDir ? 'dir-me' : (isPl ? 'dir-you' : '');
        return `<li class="${cls}">${esc(x)}</li>`;
      }).join('')
    : '<li class="sub">Пока пусто — можно просто поздороваться или спросить совет, без правок.</li>';

  // ---- настройки (не трогаем поле, которое сейчас правят) ----
  const se = state.settings || {};
  const setv = (sel, v) => { const el = $(sel); if (document.activeElement !== el) el.value = v; };
  setv('#sMult', se.length_mult ?? 1); $('#sMultV').textContent = '×' + (se.length_mult ?? 1);
  setv('#sMax', se.max_speaks ?? ''); setv('#sLeave', se.inactive_leave ?? '');
}
$('#sheet').addEventListener('click', async e => {
  const w = e.target.closest('[data-who]'); if (w) { sheetWho = w.dataset.who; renderSheet(st); return; }
  const tg = e.target.closest('[data-tab-go]'); if (tg) { selectTab(tg.dataset.tabGo); return; }
  const wo = e.target.closest('[data-who-open]'); if (wo) { sheetWho = wo.dataset.whoOpen; selectTab('hero'); renderSheet(st); return; }
  const dm = e.target.closest('[data-dismiss]');
  if (dm) {
    if (!busy && await ask({ title: `Отпустить ${dm.dataset.dismiss}?`, text: 'Спутник уйдёт из отряда. Позвать обратно можно будет только по сюжету.', ok: 'Отпустить' })) {
      closeSheet(); send('Система, отпусти ' + dm.dataset.dismiss);
    }
    return;
  }
  const c = e.target.closest('[data-cmd]'); if (c) { if (!busy) { closeSheet(); send('Система, ' + c.dataset.cmd); } return; }
  const p = e.target.closest('[data-pre]'); if (p) prefill(p.dataset.pre);
});

/* ============ boot ============ */
applyPrefs();
route();
