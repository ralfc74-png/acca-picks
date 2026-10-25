'use strict';
const TZ = 'Europe/London';
const $ = id => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem('acca.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('acca.' + k, JSON.stringify(v)); } catch (e) {} }
};
const TIER_RANK = { S: 4, A: 3, B: 2, C: 1 };
const state = {
  data: null, market: store.get('market', '1x2'), date: store.get('date', 'today'),
  league: 'all', sort: 'p', minTier: 'C', sides: 'best', frac: store.get('frac', false),
  slip: store.get('slip', [])   // [{id, fid, ...pick snapshot}]
};

// ---------- formatting
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
const fmtDay = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday: 'short', day: 'numeric', month: 'short' });
const fmtTime = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false });
const fmtYMD = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
function ymd(d) { return fmtYMD.format(d); }
function koStr(iso) { const d = new Date(iso); return fmtDay.format(d) + ' ' + fmtTime.format(d); }
function pct(p) { const x = p * 100; return x.toFixed(x < 1 ? 2 : x < 10 ? 1 : 0) + '%'; }
const FRACS = [[1,10],[1,9],[1,8],[1,7],[1,6],[2,11],[1,5],[2,9],[1,4],[2,7],[3,10],[1,3],[4,11],[2,5],[4,9],[1,2],[8,15],[4,7],[8,13],[5,8],[4,6],[8,11],[4,5],[5,6],[10,11],[1,1],[11,10],[6,5],[5,4],[11,8],[6,4],[13,8],[7,4],[15,8],[2,1],[9,4],[5,2],[11,4],[3,1],[10,3],[7,2],[4,1],[9,2],[5,1],[11,2],[6,1],[13,2],[7,1],[15,2],[8,1],[9,1],[10,1],[11,1],[12,1],[14,1],[16,1],[18,1],[20,1],[25,1],[33,1],[40,1],[50,1],[66,1],[80,1],[100,1]];
function frac(d) {
  const x = d - 1; let best = null, bd = 1e9;
  for (const [n, m] of FRACS) { const e = Math.abs(Math.log((1 + n / m) / d)); if (e < bd) { bd = e; best = [n, m]; } }
  if (d > 101) return Math.round(x) + '/1';
  return best[0] + '/' + best[1];
}
function od(d) { return d == null ? '–' : (state.frac ? frac(d) : d.toFixed(2)); }
function odBig(d) {   // for long acca prices
  if (d >= 10000) return state.frac ? Math.round(d - 1).toLocaleString('en-GB') + '/1' : d.toLocaleString('en-GB', { maximumFractionDigits: 0 });
  return od(d);
}
function money(x) { return x < 1 ? Math.round(x * 100) + 'p' : '£' + (x % 1 ? x.toFixed(2) : x); }

// ---------- load
async function load() {
  try {
    const r = await fetch('data.json?t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    state.data = await r.json();
  } catch (e) {
    $('updated').textContent = 'Could not load data.json. Pull to refresh when online.';
    return;
  }
  const d = state.data;
  $('updated').textContent = 'Picks updated ' + d.generated_at + ' (UK time) · ' + d.stats.fixtures + ' matches · ' + d.stats.picks + ' selections';
  const ageH = (Date.now() - new Date(d.generated_at_iso).getTime()) / 3600e3;
  if (ageH > 36) { $('stale').hidden = false; $('stale').textContent = 'These picks are ' + Math.round(ageH) + ' hours old. Kick-offs may have passed and prices will have moved.'; }
  $('notes').innerHTML = d.notes.map(n => '<li>' + esc(n) + '</li>').join('');
  buildControls();
  render();
}

function buildControls() {
  const d = state.data;
  $('markets').innerHTML = d.markets.map(m => `<button role="tab" data-m="${m.key}" class="${m.key === state.market ? 'on' : ''}">${esc(m.label)}</button>`).join('');
  $('markets').onclick = e => { const b = e.target.closest('button'); if (!b) return; state.market = b.dataset.m; store.set('market', state.market); buildControls(); render(); };
  const ds = [['today', 'Today'], ['tomorrow', 'Tomorrow'], ['week', 'Next 7 days']];
  $('dates').innerHTML = ds.map(([k, l]) => `<button data-d="${k}" class="${k === state.date ? 'on' : ''}">${l}</button>`).join('');
  $('dates').onclick = e => { const b = e.target.closest('button'); if (!b) return; state.date = b.dataset.d; store.set('date', state.date); buildControls(); render(); };
  const lg = $('league');
  const prev = state.league;
  lg.innerHTML = '<option value="all">All leagues</option>' + d.leagues.map(l => `<option value="${l.code}">${esc(l.name)}</option>`).join('');
  lg.value = prev;
  $('oddsFmt').textContent = state.frac ? 'Fractional' : 'Decimal';
  const m = d.markets.find(x => x.key === state.market);
  let note = '';
  if (m.odds === 'Bet365') note = 'Bet365 odds shown where football-data has them. Always check the live price.';
  else if (m.odds === 'derived') note = 'No bookmaker price in the free data. Fair odds are an estimate; implied chance is derived from the 1X2 market.';
  else note = 'No bookmaker odds in the free data for this market. Fair odds below are an ESTIMATE, not a price you can bet at.';
  if (m.boost) note += ' This market type can count towards the Bet365 acca boost (competition and price rules apply, check the terms).';
  $('marketNote').textContent = note;
}
$('league').onchange = e => { state.league = e.target.value; render(); };
$('sort').onchange = e => { state.sort = e.target.value; render(); };
$('minTier').onchange = e => { state.minTier = e.target.value; render(); };
$('sides').onchange = e => { state.sides = e.target.value; render(); };
$('oddsFmt').onclick = () => { state.frac = !state.frac; store.set('frac', state.frac); buildControls(); render(); renderSlip(); };

// ---------- filtering
function inDate(f) {
  const ko = new Date(f.kickoff), now = new Date();
  if (ko.getTime() < now.getTime() - 5 * 60e3) return false;   // already started
  if (state.date === 'week') return ko.getTime() <= now.getTime() + 7 * 864e5;
  const t = ymd(now), tm = ymd(new Date(now.getTime() + 864e5));
  return ymd(ko) === (state.date === 'today' ? t : tm);
}
function candidates() {
  const out = [];
  for (const f of state.data.fixtures) {
    if (state.league !== 'all' && f.league !== state.league) continue;
    if (!inDate(f)) continue;
    let ps = f.picks.filter(p => p.m === state.market && TIER_RANK[p.tier] >= TIER_RANK[state.minTier]);
    if (state.sides === 'best' && ps.length) { ps = [ps.slice().sort((a, b) => b.p - a.p)[0]]; }
    for (const p of ps) out.push({ f, p });
  }
  const key = {
    p: (a, b) => b.p.p - a.p.p,
    edge: (a, b) => (b.p.edge ?? -99) - (a.p.edge ?? -99) || b.p.p - a.p.p,
    ko: (a, b) => new Date(a.f.kickoff) - new Date(b.f.kickoff) || b.p.p - a.p.p
  }[state.sort];
  return out.sort(key);
}
const pid = (f, p) => f.id + '|' + p.m + '|' + p.s;
const leagueName = c => (state.data.leagues.find(l => l.code === c) || { name: c }).name;

// ---------- render list
function render() {
  const list = candidates();
  const inSlip = new Set(state.slip.map(s => s.pid));
  if (!list.length) {
    const msg = state.date === 'today' ? 'No matches left today for this filter. Try Tomorrow or Next 7 days.' : 'No picks for this filter.';
    $('list').innerHTML = '<div class="empty">' + msg + '</div>';
  } else {
    $('list').innerHTML = list.map(({ f, p }) => {
      const id = pid(f, p);
      const hasOdds = p.odds != null;
      const edgeCls = p.edge == null ? '' : (p.edge > 0 ? 'pos' : 'neg');
      return `<div class="pick ${inSlip.has(id) ? 'sel' : ''}" data-id="${esc(id)}">
        <div class="badge ${p.tier}">${p.tier}</div>
        <div><div class="match">${esc(f.home)} v ${esc(f.away)}</div>
          <div class="meta">${koStr(f.kickoff)} · ${esc(leagueName(f.league))}</div></div>
        <button class="add" aria-label="Add to slip">${inSlip.has(id) ? '✓' : '+ Add'}</button>
        <div class="sel-label" style="grid-column:2">${esc(p.label)}</div>
        <div class="stats">
          <span><span class="k">Chance</span><b>${pct(p.p)}</b></span>
          <span><span class="k">Fair odds <i class="est">est.</i></span><b>${od(p.fair)}</b></span>
          <span><span class="k">${hasOdds ? 'Bet365' : 'Bookie odds'}</span><b>${hasOdds ? od(p.odds) : '<span class="est">none in data</span>'}</b></span>
          <span><span class="k">Edge${p.imp_src && p.odds == null ? ' (derived)' : ''}</span><b class="${edgeCls}">${p.edge == null ? '–' : (p.edge > 0 ? '+' : '') + p.edge.toFixed(1) + 'pp'}</b></span>
          ${p.ev != null ? `<span><span class="k">EV per £1</span><b class="${p.ev > 0 ? 'pos' : 'neg'}">${p.ev > 0 ? '+' : ''}${(p.ev * 100).toFixed(0)}p</b></span>` : ''}
        </div>
        <div class="reason">${esc(p.reason)}${f.low_sample ? ' <span class="tag">low sample</span>' : ''}</div>
      </div>`;
    }).join('');
  }
  $('list').onclick = e => {
    const c = e.target.closest('.pick'); if (!c) return;
    const hit = list.find(x => pid(x.f, x.p) === c.dataset.id);
    if (hit) toggleSlip(hit.f, hit.p);
  };
  renderSlip();
}

// ---------- slip
function snapshot(f, p) {
  return { pid: pid(f, p), fid: f.id, match: f.home + ' v ' + f.away, ko: f.kickoff, label: p.label, m: p.m, p: p.p, fair: p.fair, odds: p.odds, tier: p.tier };
}
function toggleSlip(f, p) {
  const id = pid(f, p);
  const i = state.slip.findIndex(s => s.pid === id);
  if (i >= 0) state.slip.splice(i, 1);
  else {
    const j = state.slip.findIndex(s => s.fid === f.id);   // one selection per match
    if (j >= 0) state.slip.splice(j, 1);
    state.slip.push(snapshot(f, p));
  }
  store.set('slip', state.slip);
  render();
}
function slipMaths(legs) {
  let odds = 1, prob = 1, real = 0;
  for (const l of legs) { odds *= (l.odds != null ? l.odds : l.fair); prob *= l.p; if (l.odds != null) real++; }
  const n = legs.length;
  // tiny stake suggestion based on hit chance (never more than £2)
  const stake = prob >= 0.2 ? 2 : prob >= 0.05 ? 1 : prob >= 0.01 ? 0.5 : 0.25;
  const ev = (real === n && n) ? prob * odds - 1 : null;
  return { odds, prob, real, n, stake, ev, fairOdds: prob ? 1 / prob : 0 };
}
function renderSlip() {
  const legs = state.slip;
  const bar = $('slipBar');
  if (!legs.length) { bar.hidden = true; $('slipPanel').hidden = true; return; }
  const s = slipMaths(legs);
  bar.hidden = false;
  $('slipCount').textContent = legs.length + (legs.length === 1 ? ' pick' : '-fold');
  $('slipOdds').textContent = odBig(s.odds) + (s.real === s.n ? '' : ' est.');
  $('slipProb').textContent = pct(s.prob) + ' chance';
  $('slipLegs').innerHTML = legs.map((l, i) => `<div class="leg">
      <div><b>${esc(l.label)}</b></div><div>${od(l.odds != null ? l.odds : l.fair)}${l.odds == null ? ' <i class="est">est.</i>' : ''}</div>
      <button class="x" data-i="${i}" aria-label="Remove">✕</button>
      <div class="meta">${esc(l.match)} · ${koStr(l.ko)} · ${pct(l.p)}</div></div>`).join('');
  const oddsNote = s.real === s.n ? 'Bet365 prices (from data, check live)' : `${s.real} of ${s.n} legs have a Bet365 price; the rest use fair odds, so this is an estimate`;
  $('slipSummary').innerHTML = `<div class="sumbox">
    <div><span class="k">Combined odds</span><b>${odBig(s.odds)}${s.real === s.n ? '' : ' est.'}</b></div>
    <div><span class="k">Hit chance (if independent)</span><b>${pct(s.prob)}</b></div>
    <div><span class="k">Fair acca odds</span><b>${odBig(s.fairOdds)}</b></div>
    <div><span class="k">Tiny stake idea</span><b>${money(s.stake)}</b></div>
    <div style="grid-column:1/3"><span class="k">${esc(oddsNote)}</span></div>
    ${s.ev != null ? `<div style="grid-column:1/3"><span class="k">Expected profit per £1</span><b class="${s.ev > 0 ? 'pos' : 'neg'}">${s.ev > 0 ? '+' : ''}${(s.ev * 100).toFixed(0)}p</b></div>` : ''}
    <div style="grid-column:1/3"><span class="k">Returns on ${money(s.stake)} if all win</span><b>£${(s.stake * s.odds).toLocaleString('en-GB', { maximumFractionDigits: 2 })}</b></div>
    <div style="grid-column:1/3" class="note">Roughly 1 in ${Math.max(1, Math.round(1 / s.prob)).toLocaleString('en-GB')} chance of winning. Stake only what you can afford to lose.</div>
  </div>`;
  $('slipLegs').onclick = e => { const b = e.target.closest('.x'); if (!b) return; state.slip.splice(+b.dataset.i, 1); store.set('slip', state.slip); render(); };
}
$('slipToggle').onclick = () => { $('slipPanel').hidden = !$('slipPanel').hidden; };
$('slipClose').onclick = () => { $('slipPanel').hidden = true; };
$('slipClear').onclick = () => { if (confirm('Clear the slip?')) { state.slip = []; store.set('slip', state.slip); $('slipPanel').hidden = true; render(); } };
$('slipCopy').onclick = async () => {
  const s = slipMaths(state.slip);
  const txt = state.slip.map(l => `${l.match} (${koStr(l.ko)}): ${l.label} @ ${od(l.odds != null ? l.odds : l.fair)}${l.odds == null ? ' (est.)' : ''}`).join('\n') +
    `\n\n${state.slip.length}-fold combined ${odBig(s.odds)}${s.real === s.n ? '' : ' (est.)'}, chance ${pct(s.prob)}. Estimates only. Check prices before placing.`;
  try { await navigator.clipboard.writeText(txt); $('slipCopy').textContent = 'Copied ✓'; } catch (e) { prompt('Copy your slip:', txt); }
  setTimeout(() => $('slipCopy').textContent = 'Copy slip', 1500);
};

// ---------- auto-build
$('autoBtns').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  const n = +b.dataset.n;
  // top picks of the chosen market, one per match, using current filters (always best side)
  const saved = state.sides; state.sides = 'best';
  const c = candidates().sort((a, b) => b.p.p - a.p.p);
  state.sides = saved;
  const chosen = c.slice(0, n);
  if (!chosen.length) { $('autoMsg').textContent = 'No picks available for the current filters.'; return; }
  state.slip = chosen.map(({ f, p }) => snapshot(f, p));
  store.set('slip', state.slip);
  const s = slipMaths(state.slip);
  $('autoMsg').textContent = chosen.length < n
    ? `Only ${chosen.length} matches fit your filters (wanted ${n}). Widen the date, league or tier filter for more.`
    : `Built a ${n}-fold from the top ${n} by chance. Hit chance ${pct(s.prob)}, roughly 1 in ${Math.round(1 / s.prob).toLocaleString('en-GB')}.`;
  render();
  $('slipPanel').hidden = false;
};

// ---------- boot
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
load();
