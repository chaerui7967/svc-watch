'use strict';
/* svc.watch frontend — talks to the local FastAPI backend. */

/* ============================================================ helpers */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const last = a => (a && a.length ? a[a.length - 1] : null);
const fmtMem = mb => mb == null ? '—' : mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : Math.round(mb) + ' MB';
const fmtLat = ms => ms == null ? '—' : ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms + ' ms';
const fmtPct = v => v == null ? '—' : v.toFixed(v < 10 ? 1 : 0) + '%';
function fmtDur(sec) {
  if (sec == null || sec < 0) return '—';
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}
const sinceTs = ts => ts ? fmtDur(Date.now() / 1000 - ts) : '—';
function hhmm(ts) {
  const d = new Date(ts * 1000), today = new Date();
  const t = d.toTimeString().slice(0, 5);
  return d.toDateString() === today.toDateString() ? t : `${d.getMonth() + 1}/${d.getDate()} ${t}`;
}
const ic = (id, cls = 'i sm') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;

async function api(path, { method = 'GET', body } = {}) {
  const r = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-SvcWatch': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    let msg = data.detail;
    if (Array.isArray(msg)) msg = msg.map(d => String(d.msg || '').replace(/^Value error, /, '')).join(' · ');
    throw new Error(msg || `요청 실패 (${r.status})`);
  }
  return data;
}

function toast(msg, kind = 'info') {
  const t = document.createElement('div');
  t.className = 'toast glass';
  t.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  t.innerHTML = `${ic(kind === 'error' ? 'alert' : 'activity')}<span>${esc(msg)}</span>`;
  if (kind === 'error') t.style.borderColor = 'var(--st-crit)';
  document.body.appendChild(t);
  setTimeout(() => t.remove(), kind === 'error' ? 6000 : 2600);
}

async function withBusy(btn, fn) {
  if (btn) { btn.classList.add('busy'); btn.disabled = true; }
  try { return await fn(); }
  catch (e) { toast(e.message, 'error'); }
  finally { if (btn) { btn.classList.remove('busy'); btn.disabled = false; } }
}

/* ============================================================ status visuals */
const STATUS = {
  running: { label: '실행 중', color: 'var(--st-good)' },
  warning: { label: '경고', color: 'var(--st-warn)' },
  down:    { label: '응답 없음', color: 'var(--st-crit)' },
  stopped: { label: '중지됨', color: 'var(--st-off)' },
  unknown: { label: '확인 중', color: 'var(--st-off)' },
};
const SICON = {
  running: '<circle cx="7" cy="7" r="6" fill="currentColor"/><path d="M4 7.2l2 2 4-4.2" stroke="#0F172A" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  warning: '<path d="M7 1.2 13.2 12.4H.8Z" fill="currentColor"/><rect x="6.3" y="5" width="1.4" height="4" fill="#0F172A"/><rect x="6.3" y="10" width="1.4" height="1.4" fill="#0F172A"/>',
  down:    '<circle cx="7" cy="7" r="6" fill="currentColor"/><path d="M4.6 4.6l4.8 4.8M9.4 4.6l-4.8 4.8" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/>',
  stopped: '<circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.6"/><rect x="4.8" y="4.8" width="4.4" height="4.4" rx="1" fill="currentColor"/>',
  unknown: '<circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2 2"/>',
};
const sIcon = s => `<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" style="color:${(STATUS[s] || STATUS.unknown).color}">${SICON[s] || SICON.unknown}</svg>`;
const badge = (s, label) => `<span class="status pill ${s}">${sIcon(s)}${esc(label || (STATUS[s] || STATUS.unknown).label)}</span>`;
const TYPE = {
  svc: { label: 'Windows 서비스', short: '서비스', icon: 'cog' }, proc: { label: '프로세스', short: '프로세스', icon: 'cpu' },
  docker: { label: 'Docker', short: 'Docker', icon: 'box' }, http: { label: 'HTTP 엔드포인트', short: 'HTTP', icon: 'globe' },
  task: { label: '작업 스케줄러', short: '예약 작업', icon: 'clock' },
};

function sparkline(data, threshold = 80, w = 84, h = 24) {
  if (!data || data.length < 2) return '<span class="muted" style="font-size:12px">수집 중</span>';
  const max = Math.max(100, ...data), step = w / (data.length - 1), y = v => h - 2 - (v / max) * (h - 4);
  const pts = data.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const hot = last(data) >= threshold;
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <polygon points="0,${h - 2} ${pts} ${w},${h - 2}" fill="var(--chart-fill)"/>
    <polyline points="${pts}" fill="none" stroke="${hot ? 'var(--st-warn)' : 'var(--chart-line)'}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

/* Area chart: one axis, optional threshold line with markers, hover + keyboard tooltip. */
function areaChart(el, data, o) {
  if (!el) return;
  const { max: maxIn = 100, unit = '%', threshold = null, W = 520, H = 160, stepSec = 60, label } = o;
  if (!data || data.length < 2) { el.innerHTML = `<div class="collecting">데이터 수집 중… (잠시 후 표시돼요)</div>`; return; }
  const max = Math.max(maxIn, ...data) || 1;
  const P = { l: 46, r: 10, t: 10, b: 22 }, iw = W - P.l - P.r, ih = H - P.t - P.b;
  const x = i => P.l + (i / (data.length - 1)) * iw, y = v => P.t + ih - (v / max) * ih;
  const fmt = v => unit === 'MB' ? fmtMem(v) : v.toFixed(1) + unit;
  const tick = t => unit === 'MB' ? fmtMem(t) : Math.round(t) + unit;
  const ago = i => { const s = (data.length - 1 - i) * stepSec; return s === 0 ? '지금' : s < 60 ? `${s}초 전` : `${Math.round(s / 60)}분 전`; };
  const pts = data.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const above = threshold == null ? [] : data.map((v, i) => [v, i]).filter(([v]) => v >= threshold);
  const xl = [ago(0), ago(Math.floor((data.length - 1) / 2)), '지금'];
  el.innerHTML = `
    <div class="chart" tabindex="0" role="img" aria-label="${esc(label)}. 현재 ${fmt(last(data))}${above.length ? `, 기준 초과 ${above.length}회` : ''}. 좌우 화살표로 값 탐색">
      <svg viewBox="0 0 ${W} ${H}">
        ${[0, max / 2, max].map(t => `<line x1="${P.l}" x2="${P.l + iw}" y1="${y(t)}" y2="${y(t)}" stroke="${t === 0 ? 'var(--chart-axis)' : 'var(--chart-grid)'}"/>
          <text x="${P.l - 8}" y="${y(t) + 4}" text-anchor="end" font-size="11" font-family="Fira Code, monospace" fill="var(--color-muted-foreground)">${tick(t)}</text>`).join('')}
        ${xl.map((t, k) => `<text x="${P.l + iw * k / 2}" y="${H - 4}" font-size="11" font-family="Fira Code, monospace" fill="var(--color-muted-foreground)" text-anchor="${['start', 'middle', 'end'][k]}">${t}</text>`).join('')}
        <polygon points="${P.l},${P.t + ih} ${pts} ${P.l + iw},${P.t + ih}" fill="var(--chart-fill)"/>
        <polyline points="${pts}" fill="none" stroke="var(--chart-line)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        ${threshold != null && threshold <= max ? `<line x1="${P.l}" x2="${P.l + iw}" y1="${y(threshold)}" y2="${y(threshold)}" stroke="var(--st-warn)" stroke-dasharray="4 4" stroke-width="1.2"/>
          <text x="${P.l + 6}" y="${y(threshold) + 15}" font-size="11" font-family="Fira Code, monospace" fill="var(--warn-text)">경고 기준 ${tick(threshold)}</text>` : ''}
        ${above.map(([v, i]) => `<rect x="${x(i) - 4}" y="${y(v) - 4}" width="8" height="8" transform="rotate(45 ${x(i)} ${y(v)})" fill="var(--st-warn)" stroke="var(--color-card-solid)" stroke-width="1.5"/>`).join('')}
        <line class="xh" y1="${P.t}" y2="${P.t + ih}" stroke="var(--color-muted-foreground)" stroke-dasharray="3 3" opacity="0"/>
        <circle class="dot" r="4.5" fill="var(--chart-line)" stroke="var(--color-card-solid)" stroke-width="2" opacity="0"/>
        <rect class="hit" x="${P.l}" y="0" width="${iw}" height="${H}" fill="transparent"/>
      </svg>
      <div class="tip"></div>
    </div>
    ${threshold != null ? `<div class="legend"><span><svg width="14" height="3" aria-hidden="true"><rect width="14" height="3" rx="1.5" fill="var(--chart-line)"/></svg>${esc(label)}</span><span><svg width="16" height="3" aria-hidden="true"><path d="M0 1.5h5M8 1.5h5" stroke="var(--st-warn)" stroke-width="2"/></svg>경고 기준</span><span><svg width="10" height="10" aria-hidden="true"><rect x="2" y="2" width="6" height="6" transform="rotate(45 5 5)" fill="var(--st-warn)"/></svg>기준 초과</span></div>` : ''}`;
  const root = el.querySelector('.chart'), svg = root.querySelector('svg'), xh = root.querySelector('.xh'),
    dot = root.querySelector('.dot'), tip = root.querySelector('.tip');
  let idx = data.length - 1;
  const show = i => {
    idx = i; const r = svg.getBoundingClientRect(), px = x(i), py = y(data[i]);
    xh.setAttribute('x1', px); xh.setAttribute('x2', px); xh.setAttribute('opacity', 1);
    dot.setAttribute('cx', px); dot.setAttribute('cy', py); dot.setAttribute('opacity', 1);
    tip.style.left = (px / W * r.width) + 'px'; tip.style.top = (py / H * r.height) + 'px';
    tip.innerHTML = `<span class="muted">${ago(i)}</span> · <b>${fmt(data[i])}</b>${threshold != null && data[i] >= threshold ? ' · 기준 초과' : ''}`;
    tip.style.opacity = 1;
  };
  const hide = () => { xh.setAttribute('opacity', 0); dot.setAttribute('opacity', 0); tip.style.opacity = 0; };
  root.querySelector('.hit').onmousemove = e => {
    const r = svg.getBoundingClientRect(), sx = (e.clientX - r.left) * (W / r.width);
    show(Math.max(0, Math.min(data.length - 1, Math.round((sx - P.l) / iw * (data.length - 1)))));
  };
  root.onmouseleave = hide; root.onblur = hide; root.onfocus = () => show(idx);
  root.onkeydown = e => {
    if (e.key === 'ArrowLeft') { show(Math.max(0, idx - 1)); e.preventDefault(); }
    if (e.key === 'ArrowRight') { show(Math.min(data.length - 1, idx + 1)); e.preventDefault(); }
  };
}

function uptimeBar(slots, pct) {
  const n = 48, w = 520, gap = 2, bw = (w - gap * (n - 1)) / n;
  const bars = slots.map((st, i) => {
    const col = !st ? 'var(--color-muted)' : st === 'running' ? 'color-mix(in srgb, var(--st-good) 75%, transparent)' : STATUS[st].color;
    const ago = (24 - i / 2).toFixed(1).replace('.0', '');
    return `<rect x="${i * (bw + gap)}" y="0" width="${bw}" height="26" rx="3" fill="${col}"><title>${ago}시간 전 · ${st ? STATUS[st].label : '기록 없음'}</title></rect>`;
  }).join('');
  return `<svg viewBox="0 0 ${w} 26" style="width:100%;display:block" role="img" aria-label="최근 24시간 가용률 ${pct ?? '기록 없음'}">${bars}</svg>
    <div style="display:flex;justify-content:space-between;font:12px var(--font-mono);color:var(--color-muted-foreground);margin-top:6px"><span>24h 전</span><span>가동률 <b style="color:var(--color-foreground)">${pct == null ? '—' : pct + '%'}</b> · 대시보드 실행 중 기록</span><span>지금</span></div>`;
}

function logHtml(lines) {
  if (!lines.length) return '<span class="t">(로그 없음)</span>';
  return lines.map(l => {
    const cls = /\b(error|err|fatal|exception|fail(ed)?|오류|실패)\b/i.test(l) ? 'line-err' : /\b(warn(ing)?|경고)\b/i.test(l) ? 'line-warn' : '';
    return `<span class="${cls}">${esc(l)}</span>`;
  }).join('\n');
}

/* ============================================================ state */
const state = {
  view: 'dashboard', paused: false,
  ov: null, dk: null, fw: null, fwDraft: null, fwPreviewTimer: null,
  q: '', status: '', type: '',
  dq: '', df: '', dgroup: true,
  drawer: null, tab: 'overview', lastFocus: null,
  seenEvent: Number(localStorage.getItem('seenEvent') || 0),
};

/* ============================================================ dashboard */
async function loadOverview() {
  try {
    state.ov = await api('/api/overview');
    renderDashboard();
    notifyNewEvents();
  } catch (e) {
    $('#live').classList.add('paused');
    $('#liveText').textContent = '서버 연결 끊김';
  }
}

function renderDashboard() {
  const ov = state.ov; if (!ov) return;
  const h = ov.host, svcs = ov.services;
  $('#hostName').textContent = h.name;
  $('#hostOs').textContent = h.os;
  $('#hostUp').textContent = sinceTs(h.boot_time);
  $('#hostSpec').textContent = `${h.cpu_count}C · ${Math.round(h.mem_total_mb / 1024)}GB`;
  $('#agentBadge').innerHTML = `<svg width="8" height="8" aria-hidden="true"><circle cx="4" cy="4" r="4" fill="currentColor"/></svg>에이전트 v${esc(ov.agent.version)} · ${ov.agent.admin ? '관리자 권한' : '일반 권한'}`;
  $('#agentBadge').style.color = ov.agent.admin ? 'var(--st-good)' : 'var(--warn-text)';
  renderLive();

  const c = s => svcs.filter(x => x.status === s).length;
  const problems = c('down') + c('warning');
  $('#navSvcCount').hidden = !problems; $('#navSvcCount').textContent = problems;
  const tile = (st, label, value, sub, bar) => `<div class="tile glass" style="--bar:${bar}"><div class="label">${st ? sIcon(st) : ic('layers')}${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
  const types = new Set(svcs.map(s => s.type)).size;
  $('#kpis').innerHTML =
    tile(null, '등록된 서비스', svcs.length, svcs.length ? `${types}개 유형` : '아직 없음', 'var(--chart-line)') +
    tile('running', '실행 중', c('running'), '헬스체크 통과', 'var(--st-good)') +
    tile('warning', '경고', c('warning'), '기준 초과', 'var(--st-warn)') +
    tile('down', '응답 없음 · 중지', c('down') + c('stopped'), `응답 없음 ${c('down')} · 중지 ${c('stopped')}`, 'var(--st-crit)');

  $('#hostCpuNow').textContent = fmtPct(h.cpu);
  $('#hostMemNow').textContent = `${fmtPct(h.mem_pct)} · ${(h.mem_used_mb / 1024).toFixed(1)} GB`;
  areaChart($('#hostCpu'), h.cpu_hist, { label: '호스트 CPU', W: 420, H: 180, stepSec: 5 });
  areaChart($('#hostMem'), h.mem_hist, { label: '호스트 메모리', W: 420, H: 180, stepSec: 5 });

  $('#feed').innerHTML = ov.events.slice(0, 6).map(e => `<li>${sIcon(e.level)}<div><b>${esc(e.title)}</b><span class="muted">${esc(e.detail)}</span></div><time>${hhmm(e.ts)}</time></li>`).join('')
    || `<li class="muted" style="display:block;padding:20px 8px">아직 이벤트가 없어요. 서비스 상태가 바뀌면 여기에 기록됩니다.</li>`;
  $('#notifyBtn').hidden = !('Notification' in window) || Notification.permission !== 'default';

  renderSeg(); renderRows();
  if (state.drawer?.kind === 'svc' && state.tab === 'overview') refreshDrawer(true);
}

function renderLive() {
  const ov = state.ov; if (!ov) return;
  const age = Date.now() / 1000 - (ov.last_sample || 0);
  const stale = !ov.last_sample || age > 20;
  $('#live').classList.toggle('paused', state.paused || stale);
  const t = ov.last_sample ? new Date(ov.last_sample * 1000).toTimeString().slice(0, 8) : '—';
  $('#liveText').textContent = state.paused ? `일시정지 · 마지막 ${t}` : stale ? `지연됨 · 마지막 ${t}` : `LIVE · ${t}`;
}

function renderSeg() {
  const svcs = state.ov?.services || [];
  const opts = [['', '전체'], ['running', '실행 중'], ['warning', '경고'], ['down', '응답 없음'], ['stopped', '중지']];
  $('#statusSeg').innerHTML = opts.map(([k, l]) => {
    const n = k ? svcs.filter(s => s.status === k).length : svcs.length;
    return `<button aria-pressed="${state.status === k}" data-k="${k}">${l}<span class="n">${n}</span></button>`;
  }).join('');
}

function renderRows() {
  const svcs = state.ov?.services || [];
  if (!svcs.length) {
    $('#rows').innerHTML = `<tr><td colspan="10"><div class="empty"><b>등록된 서비스가 없어요</b>Windows 서비스, 프로세스, Docker 컨테이너, HTTP 주소를 등록해 모니터링을 시작하세요.<button class="btn accent" data-add>${ic('plus')}첫 서비스 등록</button></div></td></tr>`;
    return;
  }
  const q = state.q.toLowerCase(), order = { down: 0, warning: 1, unknown: 2, running: 3, stopped: 4 };
  const list = svcs
    .filter(s => (!state.status || s.status === state.status) && (!state.type || s.type === state.type))
    .filter(s => !q || [s.name, s.target, s.tags, (s.ports || []).join(',')].join(' ').toLowerCase().includes(q))
    .sort((a, b) => order[a.status] - order[b.status] || a.name.localeCompare(b.name));
  $('#rows').innerHTML = list.map(s => {
    const lat = s.latency;
    const canCtl = s.type !== 'http';
    const running = s.status === 'running' || s.status === 'warning';
    return `<tr data-id="${s.id}" tabindex="0" aria-selected="${state.drawer?.kind === 'svc' && state.drawer.id === s.id}">
      <td>${badge(s.status)}</td>
      <td><div class="svc"><span class="ico">${ic(TYPE[s.type].icon)}</span><div><b>${esc(s.name)}${s.web_url ? ` <a class="rowlink" href="${esc(s.web_url)}" target="_blank" rel="noopener" title="${esc(s.web_url)} 열기" aria-label="${esc(s.name)} 웹 페이지 열기">${ic('external')}</a>` : ''}</b>${s.reason && s.status !== 'running' ? `<span class="alert ${s.status === 'down' ? 'crit' : ''}" title="${esc(s.reason)}">${esc(s.reason)}</span>` : `<span title="${esc(s.target)}">${esc(s.target)}</span>`}</div></div></td>
      <td><span class="muted" style="font-size:13px">${TYPE[s.type].short}</span></td>
      <td class="num c-net">${s.pid ?? '—'}</td>
      <td class="num">${(s.ports || []).slice(0, 3).map(esc).join(', ') || '—'}${(s.ports || []).length > 3 ? ' …' : ''}</td>
      <td class="num"><div class="spark">${sparkline(s.cpu_series, s.cpu_threshold)}<span style="min-width:44px">${fmtPct(s.cpu)}</span></div></td>
      <td class="num">${fmtMem(s.mem_mb)}</td>
      <td class="num" style="${lat != null && lat > s.latency_ms ? 'color:var(--warn-text)' : ''}">${fmtLat(lat)}</td>
      <td class="num">${sinceTs(s.started_at)}</td>
      <td><div class="actions">${canCtl ? (running
        ? `<button class="btn sm icon" data-act="restart" aria-label="${esc(s.name)} 재시작" title="재시작">${ic('restart')}</button><button class="btn sm icon danger" data-act="stop" aria-label="${esc(s.name)} 중지" title="중지">${ic('stop')}</button>`
        : `<button class="btn sm" data-act="start" aria-label="${esc(s.name)} 시작">${ic('play')}시작</button>`) : ''}</div></td>
    </tr>`;
  }).join('') || `<tr><td colspan="10" class="muted" style="text-align:center;padding:36px">조건에 맞는 서비스가 없습니다.</td></tr>`;
}

async function controlService(id, action, btn) {
  const s = state.ov.services.find(x => x.id === id);
  if (action === 'stop' && !confirm(`'${s.name}'을(를) 중지할까요?`)) return;
  const label = { start: '시작', stop: '중지', restart: '재시작' }[action];
  await withBusy(btn, async () => {
    await api(`/api/services/${id}/${action}`, { method: 'POST' });
    toast(`${s.name} ${label} 요청 완료`);
    setTimeout(loadOverview, 1200);
  });
}

/* Browser notifications for services that opted in */
function notifyNewEvents() {
  const evs = state.ov?.events || [];
  const newest = evs[0]?.ts || 0;
  if (!state.seenEvent) { state.seenEvent = newest; localStorage.setItem('seenEvent', newest); return; }
  const fresh = evs.filter(e => e.ts > state.seenEvent && (e.level === 'down' || e.level === 'warning'));
  if (newest > state.seenEvent) { state.seenEvent = newest; localStorage.setItem('seenEvent', newest); }
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  for (const e of fresh.slice(0, 3)) {
    const svc = state.ov.services.find(s => s.id === e.sid);
    if (svc && svc.notify === false) continue;
    new Notification(`svc.watch · ${e.title}`, { body: e.detail || '', tag: `svc-${e.sid}-${e.level}` });
  }
}

/* ============================================================ drawer: service */
async function openService(id) {
  state.lastFocus = document.activeElement;
  state.drawer = { kind: 'svc', id };
  state.tab = 'overview';
  $('#dTabs').hidden = false;
  showDrawer();
  await refreshDrawer();
  $('#closeDrawer').focus();
  renderRows();
}

async function refreshDrawer(quiet) {
  const d = state.drawer; if (!d) return;
  if (d.kind === 'ctr') return renderContainerDrawer();
  let s;
  try { s = await api(`/api/services/${d.id}`); }
  catch (e) { if (!quiet) toast(e.message, 'error'); return; }
  if (state.drawer?.id !== d.id) return;
  d.data = s;
  $('#dStatus').innerHTML = badge(s.status);
  $('#dName').textContent = s.name;
  $('#dDesc').textContent = `${TYPE[s.type].label} · ${s.target}`;
  const running = s.status === 'running' || s.status === 'warning';
  $('#dActions').innerHTML = s.type === 'http' ? '' : running
    ? `<button class="btn sm" data-dact="restart">${ic('restart')}재시작</button><button class="btn sm danger" data-dact="stop">${ic('stop')}중지</button>`
    : `<button class="btn sm" data-dact="start">${ic('play')}시작</button>`;
  $$('#dTabs .tab').forEach(t => t.setAttribute('aria-selected', t.dataset.tab === state.tab));
  if (quiet && state.tab !== 'overview') return;
  renderServiceTab(s);
}

function renderServiceTab(s) {
  const body = $('#dBody');
  if (state.tab === 'overview') {
    const hc = s.health === 'tcp' ? `TCP 포트 ${esc(s.health_target)} · ${s.interval}초` : s.health === 'http' ? `HTTP ${esc(s.health_target)} · ${s.interval}초` : s.type === 'task' ? `포트 ${s.port} 대기 여부` : '프로세스 생존 여부';
    body.innerHTML = `
      ${s.reason && s.status !== 'running' ? `<div class="callout ${s.status}">${sIcon(s.status)}<div><b>${STATUS[s.status]?.label || ''}</b> · ${esc(s.reason)}</div></div>` : ''}
      <dl class="kv">
        <dt>대상</dt><dd><code>${esc(s.target)}</code></dd>
        <dt>PID</dt><dd class="mono">${s.pid ?? '—'}</dd>
        <dt>포트</dt><dd class="mono">${(s.ports || []).map(esc).join(', ') || '—'}</dd>
        <dt>가동시간</dt><dd class="mono">${sinceTs(s.started_at)}</dd>
        <dt>헬스체크</dt><dd>${hc}${s.latency != null ? ` · 최근 ${fmtLat(s.latency)}` : ''}</dd>
        <dt>자동 재시작</dt><dd>${s.auto_restart ? '켜짐 (1시간에 최대 3회)' : '꺼짐'}</dd>
        ${s.type === 'task' ? `<dt>작업</dt><dd>${s.task ? `${esc(s.task.state)} · 실행 계정 ${esc(s.task.user || '-')}${s.task.last_run ? ` · 마지막 실행 ${hhmm(Date.parse(s.task.last_run) / 1000)}` : ''}` : '작업 정보를 읽을 수 없어요 (SYSTEM 작업은 관리자 권한 필요)'}</dd>
          ` : ''}
        ${s.folder ? `<dt>프로젝트 폴더</dt><dd><button type="button" class="linkbtn" data-openfolder="${s.id}" title="파일 탐색기에서 열기">${ic('folder')}<code>${esc(s.folder)}</code></button></dd>` : ''}
        ${s.web_url ? `<dt>웹 주소</dt><dd><a class="linkbtn" href="${esc(s.web_url)}" target="_blank" rel="noopener" title="새 탭에서 열기">${ic('external')}<code>${esc(s.web_url)}</code></a></dd>` : ''}
        ${s.tags ? `<dt>태그</dt><dd>${esc(s.tags)}</dd>` : ''}
      </dl>
      ${s.type === 'http' ? '' : `<div><h3>CPU 사용률 · 최근 30분 <b class="mono">${fmtPct(s.cpu)}</b></h3><div id="cCpu"></div></div>
      <div><h3>메모리${s.mem_limit_mb ? ' · 한도 ' + fmtMem(s.mem_limit_mb) : ''} <b class="mono">${fmtMem(s.mem_mb)}</b></h3><div id="cMem"></div></div>`}
      <div><h3>가용성 · 최근 24시간</h3>${uptimeBar(s.avail_slots || [], s.uptime_pct)}</div>`;
    if (s.type !== 'http') {
      areaChart($('#cCpu'), s.cpu_series, { label: 'CPU 사용률', threshold: s.cpu_alert ? s.cpu_threshold : null });
      areaChart($('#cMem'), s.mem_series, { label: '메모리', unit: 'MB', max: s.mem_limit_mb || Math.max(...(s.mem_series || [1])) * 1.2 });
    }
  } else if (state.tab === 'logs') {
    body.innerHTML = `<div class="actions-row"><button class="btn sm" id="logReload">${ic('restart')}새로고침</button><span class="muted" id="logSrc" style="font-size:12px;align-self:center"></span></div>
      <div class="logs" id="logBox" tabindex="0" aria-label="로그">불러오는 중…</div>`;
    const load = async () => {
      try {
        const r = await api(`/api/services/${s.id}/logs?tail=300`);
        $('#logSrc').textContent = r.source ? `출처: ${r.source}` : '';
        $('#logBox').innerHTML = r.error && !r.lines.length ? `<span class="line-warn">${esc(r.error)}</span>` : logHtml(r.lines);
        $('#logBox').scrollTop = 1e9;
      } catch (e) { $('#logBox').innerHTML = `<span class="line-err">${esc(e.message)}</span>`; }
    };
    $('#logReload').onclick = load; load();
  } else if (state.tab === 'events') {
    body.innerHTML = s.events.length
      ? `<ul class="events">${s.events.map(e => `<li>${sIcon(e.level)}<span>${esc(e.title)}${e.detail ? ` <span class="muted">· ${esc(e.detail)}</span>` : ''}</span><time>${hhmm(e.ts)}</time></li>`).join('')}</ul>`
      : `<div class="empty">아직 이벤트가 없어요.</div>`;
  } else {
    body.innerHTML = `<form id="sForm" novalidate><div class="form" style="padding:0">${serviceFormHtml(s, true)}</div>
      <div class="actions-row" style="margin-top:16px"><button class="btn danger" type="button" id="sDelete">${ic('trash')}등록 해제</button><div class="spacer"></div>
      <button class="btn" type="button" id="sTest">${ic('plug')}연결 테스트</button><button class="btn accent" type="submit">저장</button></div>
      <div id="sResult" style="margin-top:12px"></div></form>`;
    wireServiceForm($('#sForm'), s);
    $('#sForm').onsubmit = async e => {
      e.preventDefault();
      await withBusy(e.submitter, async () => {
        await api(`/api/services/${s.id}`, { method: 'PUT', body: readServiceForm($('#sForm')) });
        toast('설정을 저장했어요'); loadOverview(); refreshDrawer();
      });
    };
    $('#sTest').onclick = () => testService($('#sForm'), $('#sResult'), $('#sTest'));
    $('#sDelete').onclick = async () => {
      if (!confirm(`'${s.name}' 등록을 해제할까요? 실제 서비스는 그대로 두고 모니터링만 멈춥니다.`)) return;
      await withBusy($('#sDelete'), async () => {
        await api(`/api/services/${s.id}`, { method: 'DELETE' });
        toast('등록을 해제했어요'); closeDrawer(); loadOverview();
      });
    };
  }
}

function showDrawer() {
  $('#drawer').classList.add('open'); $('#scrim').classList.add('open');
  $('#drawer').setAttribute('aria-hidden', 'false');
}
function closeDrawer() {
  $('#drawer').classList.remove('open'); $('#scrim').classList.remove('open');
  $('#drawer').setAttribute('aria-hidden', 'true');
  state.drawer = null;
  renderRows();
  state.lastFocus?.focus?.();
}

/* ============================================================ service form (register + settings) */
const TARGET_HINT = {
  svc: ['서비스 이름', 'MSSQLSERVER', '서비스 이름(표시 이름 아님)을 입력하거나 목록에서 고르세요.'],
  proc: ['프로세스 이름 또는 명령줄 일부', 'node.exe 또는 dist/server.js', '이름이나 명령줄에 이 글자가 들어간 프로세스를 추적합니다.'],
  docker: ['컨테이너 이름', 'redis', 'docker ps에 보이는 컨테이너 이름'],
  http: ['헬스체크 URL', 'http://localhost:8080/health', '주기적으로 요청해 응답 코드와 응답시간을 기록합니다.'],
  task: ['작업 이름', 'ClaimArchive', '작업 스케줄러(taskschd.msc)에 보이는 이름. 목록에서 고르면 포트를 자동으로 채워요.'],
};

function serviceFormHtml(s, edit) {
  const v = Object.assign({ type: 'svc', name: '', target: '', health: 'process', health_target: '', interval: 10, tags: '', notify: true,
    auto_restart: false, cpu_alert: true, cpu_threshold: 80, cpu_minutes: 5, latency_ms: 1000, start_cmd: '', cwd: '', log_file: '', port: '' }, s || {});
  const types = Object.entries(TYPE).map(([k, t]) => `<button type="button" role="radio" aria-checked="${v.type === k}" data-t="${k}" ${edit && v.type !== k ? 'disabled' : ''}>${ic(t.icon, 'i')}${t.label.replace(' 엔드포인트', '').replace('작업 스케줄러', '예약 작업')}</button>`).join('');
  return `
    <div class="field"><span class="lbl" id="typeLbl">유형</span><div class="typepick" role="radiogroup" aria-labelledby="typeLbl" data-f="typepick">${types}</div><input type="hidden" name="type" value="${v.type}"></div>
    <div class="field"><label for="f_name">표시 이름</label><input class="input" id="f_name" name="name" required maxlength="60" value="${esc(v.name)}" placeholder="예: 사내 API 서버"></div>
    <div class="field"><label for="f_target" data-f="tlabel"></label><input class="input mono" id="f_target" name="target" required list="discoverList" value="${esc(v.target)}" autocomplete="off">
      <div class="hint" data-f="thint"></div></div>
    ${edit ? '' : '<div class="importbox" data-f="importbox" hidden></div>'}
    <div class="field" data-f="portrow"><label for="f_port">서버 포트 <span class="muted" style="font-weight:400">(필수)</span></label>
      <input class="input mono" id="f_port" name="port" inputmode="numeric" value="${esc(v.port ?? '')}" placeholder="40400">
      <div class="hint">이 포트에서 대기 중인 프로세스를 이 작업의 서버로 보고 상태·CPU·메모리를 잽니다.</div></div>
    <div class="two" data-f="healthrow">
      <div class="field"><label for="f_health">헬스체크</label><select class="input" id="f_health" name="health">
        <option value="process" ${v.health === 'process' ? 'selected' : ''}>살아 있는지만 확인</option>
        <option value="tcp" ${v.health === 'tcp' ? 'selected' : ''}>TCP 포트 열림</option>
        <option value="http" ${v.health === 'http' ? 'selected' : ''}>HTTP 응답 (2xx/3xx)</option></select></div>
      <div class="field"><label for="f_interval">확인 주기</label><select class="input" id="f_interval" name="interval">
        ${[5, 10, 30, 60, 300].map(n => `<option value="${n}" ${+v.interval === n ? 'selected' : ''}>${n < 60 ? n + '초' : n / 60 + '분'}</option>`).join('')}</select></div>
    </div>
    <div class="field" data-f="htarget"><label for="f_ht" data-f="htlabel">포트</label><input class="input mono" id="f_ht" name="health_target" value="${esc(v.health_target || '')}"></div>
    <div data-f="proconly" class="form" style="padding:0">
      <div class="field"><label for="f_cmd">시작 명령 <span class="muted" style="font-weight:400">(시작/재시작 버튼에 사용)</span></label><input class="input mono" id="f_cmd" name="start_cmd" value="${esc(v.start_cmd || '')}" placeholder="예: node dist/server.js"></div>
      <div class="field"><label for="f_cwd">작업 폴더</label><input class="input mono" id="f_cwd" name="cwd" value="${esc(v.cwd || '')}" placeholder="예: C:\\apps\\api"></div>
    </div>
    <div class="two">
      <div class="field" data-f="logrow"><label for="f_log">로그 파일 <span class="muted" style="font-weight:400">(선택)</span></label><input class="input mono" id="f_log" name="log_file" value="${esc(v.log_file || '')}" placeholder="C:\\logs\\app.log"></div>
      <div class="field"><label for="f_tags">태그</label><input class="input" id="f_tags" name="tags" maxlength="100" value="${esc(v.tags)}" placeholder="backend, db"></div>
    </div>
    <fieldset class="field" style="border:0;padding:0;margin:0">
      <legend class="lbl" style="font-size:13px;font-weight:600;margin-bottom:4px">알림 · 자동 복구 · 경고 기준</legend>
      <label class="check"><input type="checkbox" name="notify" ${v.notify ? 'checked' : ''}> 문제가 생기면 브라우저 알림</label>
      <label class="check" data-f="autorow"><input type="checkbox" name="auto_restart" ${v.auto_restart ? 'checked' : ''}> 응답 없음이 되면 자동 재시작 (1시간에 최대 3회)</label>
      <label class="check" data-f="cpurow"><input type="checkbox" name="cpu_alert" ${v.cpu_alert ? 'checked' : ''}> CPU
        <input class="input mono" name="cpu_threshold" value="${v.cpu_threshold}" style="width:64px;min-height:30px" aria-label="CPU 경고 기준 퍼센트">% 이상이
        <input class="input mono" name="cpu_minutes" value="${v.cpu_minutes}" style="width:56px;min-height:30px" aria-label="지속 시간(분)">분 지속되면 경고</label>
      <label class="check" data-f="latrow">응답시간이 <input class="input mono" name="latency_ms" value="${v.latency_ms}" style="width:80px;min-height:30px" aria-label="응답시간 기준(ms)">ms를 넘으면 경고</label>
    </fieldset>`;
}

function wireServiceForm(form, s) {
  const typeIn = form.querySelector('[name=type]');
  const sync = () => {
    const t = typeIn.value, [l, p, h] = TARGET_HINT[t];
    form.querySelector('[data-f=tlabel]').textContent = l;
    form.querySelector('[name=target]').placeholder = p;
    form.querySelector('[data-f=thint]').textContent = h;
    const health = form.querySelector('[name=health]');
    form.querySelector('[data-f=healthrow] .field').hidden = t === 'http';
    const hv = t === 'http' ? 'http' : health.value;
    const ht = form.querySelector('[data-f=htarget]');
    ht.hidden = t === 'http' || hv === 'process';
    form.querySelector('[data-f=htlabel]').textContent = hv === 'tcp' ? '확인할 포트' : '확인할 URL';
    form.querySelector('[name=health_target]').placeholder = hv === 'tcp' ? '8080' : 'http://localhost:8080/health';
    form.querySelector('[data-f=proconly]').hidden = t !== 'proc';
    form.querySelector('[data-f=logrow]').hidden = t === 'docker' || t === 'http';
    form.querySelector('[data-f=autorow]').hidden = t === 'http';
    form.querySelector('[data-f=cpurow]').hidden = t === 'http';
    form.querySelector('[data-f=latrow]').hidden = hv === 'process';
    form.querySelector('[data-f=portrow]').hidden = t !== 'task';
    const tcpOpt = health.querySelector('option[value=tcp]'), procOpt = health.querySelector('option[value=process]');
    tcpOpt.hidden = t === 'task';
    if (t === 'task' && health.value === 'tcp') health.value = 'process';
    procOpt.textContent = t === 'task' ? '포트 열림 확인' : '살아 있는지만 확인';
    if (t === 'task') form.querySelector('[data-f=logrow]').hidden = false;
    const ib = form.querySelector('[data-f=importbox]');
    if (ib) { ib.hidden = t !== 'task'; if (t === 'task') renderImportBox(form, ib); }
    loadDiscover(t);
  };
  form.querySelectorAll('[data-f=typepick] button').forEach(b => b.onclick = () => {
    form.querySelectorAll('[data-f=typepick] button').forEach(x => x.setAttribute('aria-checked', x === b));
    typeIn.value = b.dataset.t; sync();
  });
  form.querySelector('[name=health]').onchange = sync;
  form.querySelector('[name=target]').addEventListener('change', () => {
    if (typeIn.value !== 'task') return;
    const hit = (discoverCache.task?.items || []).find(i => i.name.toLowerCase() === form.querySelector('[name=target]').value.trim().toLowerCase());
    if (!hit) return;
    if (!form.querySelector('[name=port]').value && hit.port) form.querySelector('[name=port]').value = hit.port;
    if (!form.querySelector('[name=name]').value) form.querySelector('[name=name]').value = hit.description || hit.name;
  });
  sync();
}

const discoverCache = {};
async function loadDiscover(t) {
  const dl = $('#discoverList');
  if (t === 'http') { dl.innerHTML = ''; return; }
  try {
    let items = discoverCache[t] || (discoverCache[t] = await api(`/api/discover/${t}`));
    if (t === 'task') items = items.items;
    dl.innerHTML = items.slice(0, 400).map(i => t === 'task'
      ? `<option value="${esc(i.name)}">${esc(i.description || i.state)}${i.port ? ' · 포트 ' + i.port : ''}</option>`
      : t === 'svc'
      ? `<option value="${esc(i.name)}">${esc(i.display_name)} · ${esc(i.status)}</option>`
      : t === 'proc' ? `<option value="${esc(i.name)}">${esc(i.cmdline.slice(0, 120))}</option>`
      : `<option value="${esc(i.name)}">${esc(i.image)} · ${esc(i.state)}</option>`).join('');
  } catch { dl.innerHTML = ''; }
}

/* Bulk import of scheduled tasks whose server port could be detected. */
async function renderImportBox(form, box) {
  box.innerHTML = '<div class="head">작업 스케줄러에서 가져오기</div><div class="muted" style="font-size:13px">작업 목록을 읽는 중…</div>';
  let d;
  try { d = discoverCache.task || (discoverCache.task = await api('/api/discover/task')); }
  catch (e) { box.innerHTML = `<div class="form-err">${esc(e.message)}</div>`; return; }
  if (form.querySelector('[name=type]').value !== 'task') return;
  const withPort = d.items.filter(i => i.port), noPort = d.items.filter(i => !i.port);
  const fresh = withPort.filter(i => !i.registered);
  const item = i => `<li class="${i.registered ? 'done' : ''}"><label>
      <input type="checkbox" data-imp="${esc(i.name)}" ${i.registered || !i.port ? 'disabled' : 'checked'}>
      <span><b>${esc(i.description || i.name)}</b><small title="${esc(i.root || '')}">${esc(i.name)} · ${esc(i.root || '')}</small></span>
      <span class="mono" style="font-size:12px">${i.registered ? '<span class="tag good">등록됨</span>' : i.port ? `${i.listening ? sIcon('running') : sIcon('stopped')} :${i.port}` : '<span class="muted">포트 모름</span>'}</span>
    </label></li>`;
  box.innerHTML = `
    <div class="head">${ic('clock')}작업 스케줄러에서 가져오기 <span class="muted" style="font-weight:400">· 서버 포트를 찾은 작업 ${withPort.length}개</span><span class="spacer"></span>
      ${fresh.length ? `<button type="button" class="btn sm accent" data-f="impbtn">${ic('plus')}선택한 작업 등록</button>` : ''}</div>
    ${d.items.length ? `<ul>${withPort.map(item).join('')}${noPort.map(item).join('')}</ul>` : '<div class="muted" style="font-size:13px">가져올 작업이 없어요.</div>'}
    ${d.admin ? '' : `<div class="hint">관리자 권한으로 등록한 SYSTEM 작업(예: ClaimArchive)은 지금 목록에 안 보여요. 아래 칸에 작업 이름과 포트를 직접 입력하면 등록할 수 있어요.</div>`}`;
  const btn = box.querySelector('[data-f=impbtn]');
  if (!btn) return;
  const sync = () => {
    const n = box.querySelectorAll('[data-imp]:checked').length;
    btn.disabled = !n; btn.innerHTML = `${ic('plus')}선택한 작업 ${n}개 등록`;
  };
  box.onchange = sync; sync();
  btn.onclick = () => withBusy(btn, async () => {
    const picked = [...box.querySelectorAll('[data-imp]:checked')].map(c => d.items.find(i => i.name === c.dataset.imp));
    let ok = 0;
    for (const i of picked) {
      try {
        await api('/api/services', { method: 'POST', body: { type: 'task', name: (i.description || i.name).slice(0, 60), target: i.name, port: i.port, health: 'process', interval: 10 } });
        ok++;
      } catch (e) { toast(`${i.name}: ${e.message}`, 'error'); }
    }
    delete discoverCache.task;
    closeModal();
    toast(`작업 ${ok}개를 등록했어요 · 몇 초 뒤 상태가 표시돼요`);
    setView('dashboard'); loadOverview();
  });
}

function readServiceForm(form) {
  const f = n => form.querySelector(`[name=${n}]`);
  const type = f('type').value;
  const health = type === 'http' ? 'http' : f('health').value;
  return {
    type, name: f('name').value.trim(), target: f('target').value.trim(), health,
    health_target: health === 'process' ? null : (type === 'http' ? f('target').value.trim() : f('health_target').value.trim()),
    interval: +f('interval').value, tags: f('tags').value.trim(), notify: f('notify').checked,
    auto_restart: f('auto_restart').checked, cpu_alert: f('cpu_alert').checked,
    cpu_threshold: +f('cpu_threshold').value || 80, cpu_minutes: +f('cpu_minutes').value || 5,
    latency_ms: +f('latency_ms').value || 1000,
    port: type === 'task' ? (+f('port').value || null) : null,
    start_cmd: f('start_cmd').value.trim() || null, cwd: f('cwd').value.trim() || null, log_file: f('log_file').value.trim() || null,
  };
}

async function testService(form, out, btn) {
  out.innerHTML = '';
  await withBusy(btn, async () => {
    const r = await api('/api/test', { method: 'POST', body: readServiceForm(form) });
    out.innerHTML = `<div class="inline-result ${r.ok ? 'ok' : 'bad'}">${sIcon(r.ok ? 'running' : 'down')} ${esc(r.message)}</div>`;
  });
}

function openModal(prefill) {
  state.lastFocus = document.activeElement;
  $('#mBody').innerHTML = serviceFormHtml(prefill, false) + '<div id="mResult"></div>';
  wireServiceForm($('#mForm'), prefill);
  $('#modal').classList.add('open');
  $('#f_name').focus();
}
function closeModal() { $('#modal').classList.remove('open'); state.lastFocus?.focus?.(); }

$('#mForm').onsubmit = async e => {
  e.preventDefault();
  await withBusy($('#saveModal'), async () => {
    const body = readServiceForm($('#mForm'));
    await api('/api/services', { method: 'POST', body });
    closeModal(); toast(`'${body.name}' 등록 완료 · 몇 초 뒤 상태가 표시돼요`);
    if (body.notify && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission();
    setView('dashboard'); loadOverview();
  });
};
$('#mTest').onclick = () => testService($('#mForm'), $('#mResult'), $('#mTest'));

/* ============================================================ docker */
const DK = c => c.state === 'running' ? (c.health === 'unhealthy' ? ['warning', 'unhealthy'] : ['running', c.health === 'healthy' ? 'healthy' : '실행 중'])
  : c.state === 'restarting' ? ['warning', '재시작 중'] : c.state === 'paused' ? ['warning', '일시정지']
  : (c.state === 'exited' || c.state === 'dead') && c.exit_code ? ['down', `종료 · ${c.exit_code}${c.oom || c.exit_code === 137 ? ' OOM' : ''}`] : ['stopped', c.state === 'created' ? '생성됨' : '중지됨'];
function dkMatch(c, f) { const k = DK(c)[0]; return !f || (f === 'problem' ? (k === 'warning' || k === 'down') : k === f); }

async function loadDocker() {
  try { state.dk = await api('/api/docker'); renderDocker(); }
  catch (e) { toast(e.message, 'error'); }
}

function renderDocker() {
  const d = state.dk; if (!d) return;
  const cs = d.containers || [];
  const probs = cs.filter(c => dkMatch(c, 'problem')).length;
  $('#navDkCount').hidden = !probs; $('#navDkCount').textContent = probs;
  $('#dkBanner').innerHTML = d.engine.ok ? '' : `<div class="banner warn">${ic('alert', 'i')}<div class="grow"><b>Docker 엔진에 연결되지 않았어요</b>${esc(d.engine.error || '')}</div><button class="btn sm" id="dkRetry">${ic('restart')}다시 확인</button></div>`;
  if ($('#dkRetry')) $('#dkRetry').onclick = () => withBusy($('#dkRetry'), loadDocker);
  const n = k => cs.filter(c => DK(c)[0] === k).length;
  const imgs = (d.df || []).find(x => x.type === 'Images'), vols = (d.df || []).find(x => x.type === 'Local Volumes');
  const projects = new Set(cs.map(c => c.project).filter(Boolean)).size;
  $('#dkStrip').innerHTML = [
    ['Docker 엔진', d.engine.ok ? `${sIcon('running')} 실행 중` : `${sIcon('down')} 연결 안 됨`],
    ['버전', d.engine.version ? `${esc(d.engine.version)} <small>· ${esc(d.engine.os || '')}</small>` : '—'],
    ['컨테이너', `${cs.length} <small>실행 ${n('running')} · 문제 ${probs} · 중지 ${n('stopped')}</small>`],
    ['이미지', imgs ? `${imgs.count} <small>· ${esc(imgs.size)}</small>` : '—'],
    ['볼륨', vols ? `${vols.count} <small>· ${esc(vols.size)}</small>` : '—'],
    ['Compose 프로젝트', projects],
  ].map(([k, v]) => `<div class="item"><span>${k}</span><b>${v}</b></div>`).join('');

  const cnt = f => cs.filter(c => dkMatch(c, f)).length;
  $('#dkSeg').innerHTML = [['', '전체'], ['running', '실행 중'], ['problem', '문제'], ['stopped', '중지']]
    .map(([k, l]) => `<button aria-pressed="${state.df === k}" data-k="${k}">${l}<span class="n">${cnt(k)}</span></button>`).join('');
  renderDkRows();

  const df = d.df || [], maxMb = Math.max(1, ...df.map(x => x.size_mb || 0));
  const dfName = { Images: '이미지', Containers: '컨테이너', 'Local Volumes': '볼륨', 'Build Cache': '빌드 캐시' };
  $('#dkDisk').innerHTML = df.map(x => `<li><div class="grow">${dfName[x.type] || esc(x.type)}<span>${x.count}개 · 회수 가능 ${esc(x.reclaimable)}</span></div><span class="meter" style="width:140px" aria-hidden="true"><i style="width:${(x.size_mb || 0) / maxMb * 100}%"></i></span><b class="mono" style="min-width:72px;text-align:right">${esc(x.size)}</b></li>`).join('')
    || `<li class="muted">${d.engine.ok ? '불러오는 중…' : 'Docker 엔진 연결 후 표시돼요'}</li>`;
  $('#dkEvents').innerHTML = (d.events || []).slice(0, 8).map(e => `<li>${sIcon(e.level)}<div><b>${esc(e.title)}</b><span class="muted">${esc(e.detail)}</span></div><time>${hhmm(e.ts)}</time></li>`).join('')
    || `<li class="muted" style="display:block;padding:16px 8px">아직 이벤트가 없어요.</li>`;
  if (state.drawer?.kind === 'ctr') renderContainerDrawer(true);
}

function renderDkRows() {
  const d = state.dk; if (!d) return;
  if (!d.engine.ok) { $('#dkRows').innerHTML = `<tr><td colspan="9"><div class="empty">Docker Desktop을 실행하면 컨테이너가 여기에 표시돼요.</div></td></tr>`; return; }
  const q = state.dq.toLowerCase(), order = { down: 0, warning: 1, running: 2, stopped: 3 };
  const list = d.containers.filter(c => dkMatch(c, state.df))
    .filter(c => !q || [c.name, c.image, c.project, (c.ports || []).join(',')].join(' ').toLowerCase().includes(q))
    .sort((a, b) => order[DK(a)[0]] - order[DK(b)[0]] || a.name.localeCompare(b.name));
  const registered = new Set((state.ov?.services || []).filter(s => s.type === 'docker').map(s => s.target));
  const row = c => {
    const [k, l] = DK(c), pct = c.mem_mb != null && c.mem_limit_mb ? c.mem_mb / c.mem_limit_mb * 100 : null;
    const alert = k === 'down' ? (c.oom || c.exit_code === 137 ? '메모리 부족(OOM)으로 종료' : `비정상 종료 · exit ${c.exit_code}`)
      : k === 'warning' ? (c.health === 'unhealthy' ? '컨테이너 헬스체크 실패' : `재시작 ${c.restarts}회`) : null;
    return `<tr data-cname="${esc(c.name)}" tabindex="0" aria-selected="${state.drawer?.kind === 'ctr' && state.drawer.name === c.name}">
      <td>${badge(k, l)}</td>
      <td><div class="svc"><span class="ico">${ic('box')}</span><div><b>${esc(c.name)}${registered.has(c.name) ? '<span class="tag good">감시 중</span>' : ''}</b>${alert ? `<span class="alert ${k === 'down' ? 'crit' : ''}">${alert}</span>` : `<span>${esc(c.image)}</span>`}</div></div></td>
      <td>${(c.ports || []).map(p => `<code class="pcode">${esc(p)}</code>`).join(' ') || '<span class="muted">—</span>'}</td>
      <td class="num"><div class="spark">${c.state === 'running' ? sparkline(c.cpu_series) : ''}<span style="min-width:44px">${fmtPct(c.cpu)}</span></div></td>
      <td class="num"><div class="memcell">${pct != null ? `<span class="meter ${pct >= 80 ? 'hot' : ''}" aria-hidden="true"><i style="width:${Math.min(100, pct).toFixed(0)}%"></i></span>` : ''}<span>${c.mem_mb != null ? fmtMem(c.mem_mb) + (c.mem_limit_mb ? ' / ' + fmtMem(c.mem_limit_mb) : '') : '—'}</span></div></td>
      <td class="num c-net" style="font-size:13px">${esc(c.net || '—')}</td>
      <td class="num" style="${c.restarts >= 3 ? 'color:var(--warn-text)' : ''}">${c.restarts}</td>
      <td class="num">${c.state === 'running' ? sinceTs(c.started_at) : '—'}</td>
      <td><div class="actions">${c.state === 'running' || c.state === 'restarting'
        ? `<button class="btn sm icon" data-cact="restart" aria-label="${esc(c.name)} 재시작" title="재시작">${ic('restart')}</button><button class="btn sm icon danger" data-cact="stop" aria-label="${esc(c.name)} 중지" title="중지">${ic('stop')}</button>`
        : `<button class="btn sm icon" data-cact="start" aria-label="${esc(c.name)} 시작" title="시작">${ic('play')}</button>`}</div></td>
    </tr>`;
  };
  let html = '';
  if (state.dgroup) {
    const groups = {};
    list.forEach(c => (groups[c.project || '독립 컨테이너'] ??= []).push(c));
    for (const [g, gs] of Object.entries(groups))
      html += `<tr class="group"><td colspan="9">${ic('layers')}${esc(g)} <span style="font-weight:400">· ${gs.length}개</span></td></tr>` + gs.map(row).join('');
  } else html = list.map(row).join('');
  $('#dkRows').innerHTML = html || `<tr><td colspan="9"><div class="empty">${d.containers.length ? '조건에 맞는 컨테이너가 없습니다.' : '컨테이너가 없어요.'}</div></td></tr>`;
}

async function controlContainer(name, action, btn) {
  if (action === 'stop' && !confirm(`컨테이너 '${name}'을(를) 중지할까요?`)) return;
  await withBusy(btn, async () => {
    await api(`/api/docker/${encodeURIComponent(name)}/${action}`, { method: 'POST' });
    toast(`${name} ${{ start: '시작', stop: '중지', restart: '재시작' }[action]} 완료`);
    setTimeout(loadDocker, 800);
  });
}

function openContainer(name) {
  state.lastFocus = document.activeElement;
  state.drawer = { kind: 'ctr', name };
  $('#dTabs').hidden = true;
  showDrawer();
  renderContainerDrawer();
  $('#closeDrawer').focus();
  renderDkRows();
}

async function renderContainerDrawer(quiet) {
  const name = state.drawer?.name;
  const c = state.dk?.containers.find(x => x.name === name);
  if (!c) { if (!quiet) toast('컨테이너를 찾을 수 없어요', 'error'); return; }
  const [k, l] = DK(c);
  $('#dStatus').innerHTML = badge(k, l);
  $('#dName').textContent = c.name;
  $('#dDesc').textContent = `Docker · ${c.image}`;
  const running = c.state === 'running' || c.state === 'restarting';
  $('#dActions').innerHTML = running
    ? `<button class="btn sm" data-dcact="restart">${ic('restart')}재시작</button><button class="btn sm danger" data-dcact="stop">${ic('stop')}중지</button>`
    : `<button class="btn sm" data-dcact="start">${ic('play')}시작</button>`;
  if (quiet) {  // live refresh: only charts and header, keep log scroll position
    areaChart($('#cCpu'), c.cpu_series, { label: 'CPU 사용률', threshold: 80 });
    areaChart($('#cMem'), c.mem_series, { label: '메모리', unit: 'MB', max: c.mem_limit_mb || 0 });
    return;
  }
  const registered = (state.ov?.services || []).some(s => s.type === 'docker' && s.target === c.name);
  $('#dBody').innerHTML = `
    <dl class="kv">
      <dt>컨테이너 ID</dt><dd><code>${esc(c.id)}</code></dd>
      <dt>이미지</dt><dd><code>${esc(c.image)}</code></dd>
      <dt>Compose</dt><dd>${esc(c.project || '—')}</dd>
      <dt>포트</dt><dd>${(c.ports || []).map(p => `<code class="pcode">${esc(p)}</code>`).join(' ') || '—'}</dd>
      <dt>재시작 정책</dt><dd>${esc(c.restart_policy)} · 재시작 <span class="mono">${c.restarts}</span>회</dd>
      <dt>헬스체크</dt><dd>${esc(c.health || '설정 안 됨')}</dd>
      <dt>볼륨</dt><dd>${(c.volumes || []).map(v => `<code>${esc(v)}</code>`).join(' ') || '—'}</dd>
      <dt>상태</dt><dd>${esc(c.status_text || c.state)}</dd>
    </dl>
    ${registered ? '' : `<div class="banner info" style="padding:10px 14px">${ic('bell', 'i')}<div class="grow">이 컨테이너를 대시보드에 등록하면 상태 변화 알림과 자동 재시작을 쓸 수 있어요.</div><button class="btn sm accent" id="cRegister">${ic('plus')}등록</button></div>`}
    <div><h3>CPU · 최근 30분 <b class="mono">${fmtPct(c.cpu)}</b></h3><div id="cCpu"></div></div>
    <div><h3>메모리${c.mem_limit_mb ? ' · 한도 ' + fmtMem(c.mem_limit_mb) : ''} <b class="mono">${fmtMem(c.mem_mb)}</b></h3><div id="cMem"></div></div>
    <div><h3>로그 · docker logs --tail 300 <button class="btn sm" id="cLogReload" style="min-height:26px">${ic('restart')}새로고침</button></h3><div class="logs" id="cLogs" tabindex="0" aria-label="컨테이너 로그">불러오는 중…</div></div>`;
  areaChart($('#cCpu'), c.cpu_series, { label: 'CPU 사용률', threshold: 80 });
  areaChart($('#cMem'), c.mem_series, { label: '메모리', unit: 'MB', max: c.mem_limit_mb || 0 });
  if ($('#cRegister')) $('#cRegister').onclick = () => { closeDrawer(); openModal({ type: 'docker', name: c.name, target: c.name }); };
  const load = async () => {
    try {
      const r = await api(`/api/docker/${encodeURIComponent(c.name)}/logs?tail=300`);
      $('#cLogs').innerHTML = r.error ? `<span class="line-warn">${esc(r.error)}</span>` : logHtml(r.lines);
      $('#cLogs').scrollTop = 1e9;
    } catch (e) { $('#cLogs').innerHTML = `<span class="line-err">${esc(e.message)}</span>`; }
  };
  $('#cLogReload').onclick = load; load();
}

/* ============================================================ firewall */
const isPrivateIp = cidr => /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.|169\.254\.|f[cd][0-9a-f]{2}:|fe80:)/i.test(cidr);
const fwKey = c => c ? JSON.stringify({ m: c.mode, a: c.allow.map(({ id, ...r }) => r), e: c.exposure, d: [...c.disable_rules].sort() }) : null;

async function loadFirewall(quiet) {
  try {
    state.fw = await api('/api/firewall');
    state.fwDraft = structuredClone(state.fw.config);
    renderFirewall();
    loadPreview();
  } catch (e) { if (!quiet) toast(e.message, 'error'); }
}

async function saveDraft() {
  try {
    state.fwDraft = await api('/api/firewall/config', { method: 'PUT', body: state.fwDraft });
    state.fw.config = structuredClone(state.fwDraft);
    renderFirewall(); loadPreview();
    return true;
  } catch (e) {
    toast(e.message, 'error');
    state.fwDraft = structuredClone(state.fw.config);
    renderFirewall();
    return false;
  }
}

function loadPreview() {
  clearTimeout(state.fwPreviewTimer);
  state.fwPreviewTimer = setTimeout(async () => {
    try {
      const r = await api('/api/firewall/preview');
      $('#fwCode').innerHTML = esc(r.script)
        .replace(/^(#.*)$/gm, '<span class="c">$1</span>')
        .replace(/^([A-Z][a-zA-Z]+-NetFirewall[A-Za-z]+)/gm, '<span class="k">$1</span>');
    } catch (e) { $('#fwCode').textContent = e.message; }
  }, 150);
}

function renderFirewall() {
  const fw = state.fw, d = state.fwDraft; if (!fw || !d) return;
  const profiles = fw.status.profiles || [];
  const on = profiles.filter(p => p.enabled === 'True');
  const off = profiles.filter(p => p.enabled !== 'True').map(p => p.name);
  const applied = fw.applied;
  const activeAllow = d.allow.filter(a => a.on);
  const blockedTotal = (fw.blocked || []).reduce((s, b) => s + b.count, 0);

  const tile = (st, l, v, sub, bar) => `<div class="tile glass" style="--bar:${bar}"><div class="label">${st ? sIcon(st) : ic('shield')}${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`;
  $('#fwKpis').innerHTML =
    tile(off.length ? 'warning' : 'running', 'Windows 방화벽', off.length ? '일부 꺼짐' : '켜짐', `${on.length}/${profiles.length} 프로필 켜짐${off.length ? ' · 꺼짐: ' + off.join(', ') : ''}`, off.length ? 'var(--st-warn)' : 'var(--st-good)') +
    tile(applied?.mode ? 'running' : 'stopped', '외부 → 이 PC 접속', applied?.mode ? '차단' : '기본값', applied?.mode ? `허용 목록 모드 · 적용 ${hhmm(fw.applied_at)}` : '허용 목록 모드 미적용', applied?.mode ? 'var(--st-good)' : 'var(--st-off)') +
    tile(null, '허용 IP 규칙', activeAllow.length, `전체 ${d.allow.length}개 · 인터넷 IP ${activeAllow.filter(a => !isPrivateIp(a.cidr)).length}개`, 'var(--chart-line)') +
    tile('down', '최근 차단된 접속', fw.blocked ? blockedTotal.toLocaleString() : '—', fw.blocked ? `${fw.blocked.length}개 출처 · 방화벽 로그` : '관리자 권한 필요', 'var(--st-crit)');

  const banners = [];
  if (!fw.admin) banners.push(`<div class="banner warn">${ic('alert', 'i')}<div class="grow"><b>관리자 권한이 아니라서 적용할 수 없어요</b>설정과 미리보기는 지금도 할 수 있어요. 실제로 적용하려면 대시보드를 끄고 <code>run-admin.bat</code>으로 다시 실행하세요.</div></div>`);
  if (d.mode && off.length) banners.push(`<div class="banner warn">${ic('alert', 'i')}<div class="grow"><b>지금 꺼져 있는 ${off.join(', ')} 프로필이 켜집니다</b>적용하면 이 PC의 모든 네트워크 프로필에서 방화벽이 켜지고, 들어오는 연결은 허용 목록만 통과해요. 현재 연결된 네트워크: ${esc([...new Set(fw.status.active || [])].join(', ') || '알 수 없음')}</div></div>`);
  const rdp = d.exposure.find(e => e.port === 3389);
  if (d.mode && (!rdp || rdp.mode === 'local')) banners.push(`<div class="banner warn">${ic('alert', 'i')}<div class="grow"><b>원격 데스크톱(3389)은 외부에서 막혀요</b>다른 PC에서 원격으로 접속 중이라면 적용하는 순간 연결이 끊깁니다. 원격 접속이 필요하면 아래에서 3389를 '허용 목록'으로 바꾸고 접속할 IP를 추가하세요. (60초 자동 복구가 켜져 있으면 끊겨도 원래대로 돌아옵니다.)</div></div>`);
  banners.push(d.mode
    ? `<div class="banner info">${ic('shield', 'i')}<div class="grow"><b>허용 목록 모드</b>인터넷에서 이 PC로 들어오는 연결은 모두 막고, 아래 목록의 IP만 지정한 포트로 접속할 수 있어요. 이 PC에서 밖으로 나가는 연결(웹 브라우징 등)은 영향이 없습니다.</div><label class="switch"><input type="checkbox" id="fwMode" checked aria-label="허용 목록 모드"><span></span></label></div>`
    : `<div class="banner off">${ic('shield', 'i')}<div class="grow"><b>허용 목록 모드 꺼짐</b>${applied?.mode ? '적용하면 svc.watch 규칙을 지우고 끈 규칙을 다시 켭니다.' : 'Windows 기본 방화벽 규칙을 그대로 사용 중이에요. 켜고 적용하면 허용한 IP만 들어올 수 있어요.'}</div><label class="switch"><input type="checkbox" id="fwMode" aria-label="허용 목록 모드"><span></span></label></div>`);
  $('#fwBanners').innerHTML = banners.join('');

  const allowPorts = d.exposure.filter(e => e.mode === 'allow');
  const curSel = $('#fwPorts').value;
  $('#fwPorts').innerHTML = `<option value="all">허용 목록 포트 전체${allowPorts.length ? ` (${allowPorts.map(e => e.port).join(', ')})` : ''}</option>` +
    allowPorts.map(e => `<option value="${e.port}">${esc(e.label || '포트')} (${e.port})</option>`).join('') + `<option value="custom">직접 입력…</option>`;
  if ([...$('#fwPorts').options].some(o => o.value === curSel)) $('#fwPorts').value = curSel;

  const today = new Date().toISOString().slice(0, 10);
  $('#fwRows').innerHTML = d.allow.map(a => {
    const priv = isPrivateIp(a.cidr), expired = a.expires && a.expires < today;
    return `<tr>
      <td><label class="switch"><input type="checkbox" data-tog="${a.id}" ${a.on ? 'checked' : ''} aria-label="${esc(a.name)} 규칙 사용"><span></span></label></td>
      <td><b style="font-weight:600">${esc(a.name)}</b></td>
      <td><code class="pcode">${esc(a.cidr)}</code></td>
      <td class="mono" style="font-size:13px">${a.ports === 'all' ? '<span class="muted">전체</span>' : a.ports.join(', ')}</td>
      <td><span class="scope ${priv ? '' : 'net'}">${ic(priv ? 'server' : 'globe')}${priv ? 'LAN' : '인터넷'}</span></td>
      <td><input type="date" class="input mono" data-exp="${a.id}" value="${a.expires || ''}" style="min-height:30px;font-size:12px;${expired ? 'border-color:var(--st-crit)' : ''}" aria-label="${esc(a.name)} 만료일" title="${expired ? '만료됨 · 다음 적용 때 제외돼요' : '만료일 (선택)'}"></td>
      <td><button class="btn sm icon danger" data-del="${a.id}" aria-label="${esc(a.name)} 삭제" title="삭제">${ic('trash')}</button></td>
    </tr>`;
  }).join('') || `<tr><td colspan="7" class="muted" style="text-align:center;padding:24px">허용된 IP가 없어요. 이대로 적용하면 외부 접속이 모두 차단됩니다.</td></tr>`;

  const sugg = (fw.suggested_exposure || []).filter(s => !d.exposure.some(e => e.port === s.port));
  $('#fwExpo').innerHTML = d.exposure.map((e, i) => `<li>
      <div class="grow">${esc(e.label || '이름 없음')}<span>TCP ${e.port}</span></div>
      <div class="seg sm" role="group" aria-label="${esc(e.label)} ${e.port} 노출 범위">
        <button type="button" data-expo="${i}" data-m="local" aria-pressed="${e.mode === 'local'}">로컬만</button>
        <button type="button" data-expo="${i}" data-m="allow" aria-pressed="${e.mode === 'allow'}">허용 목록</button>
      </div>
      <button class="btn sm icon" data-expodel="${i}" aria-label="${e.port} 목록에서 제거" title="목록에서 제거">${ic('x')}</button></li>`).join('')
    + sugg.map(s => `<li style="opacity:.8"><div class="grow">${esc(s.label)}<span>TCP ${s.port} · 감지됨</span></div><button class="btn sm" data-sugg="${s.port}" data-label="${esc(s.label)}">${ic('plus')}목록에 추가</button></li>`).join('')
    || `<li class="muted">등록된 서비스가 포트를 열면 여기에 표시돼요. 직접 추가할 수도 있어요.</li>`;

  $('#fwBlocked').innerHTML = fw.blocked?.length
    ? fw.blocked.slice(0, 8).map(b => `<li>${ic('ban')}<div><b class="mono">${esc(b.ip)}</b><span class="muted">${esc(b.last.slice(5))} · ${esc(b.protocol)} ${esc(b.port)} · ${b.count}회 · ${b.private ? 'LAN' : '인터넷'}</span></div><button class="btn sm" data-allowip="${esc(b.ip)}">허용</button></li>`).join('')
    : `<li class="muted" style="display:block;padding:16px 8px">${esc(fw.blocked_note || '차단 기록이 없어요')}</li>`;

  const conf = fw.conflicts;
  const names = d.rule_names || {};
  const scanned = new Map((conf.items || []).map(r => [r.id, r]));
  const ids = [...new Set([...(conf.items || []).map(r => r.id), ...d.disable_rules, ...fw.disabled_by_us])];
  $('#fwConf').innerHTML = !conf.scanned_at && !ids.length
    ? `<li class="muted" style="display:block">${fw.admin ? "아직 검사하지 않았어요. '검사'를 누르면 이 PC의 방화벽 규칙을 읽어 옵니다 (10~30초)." : '방화벽 규칙 목록은 관리자 권한으로 실행할 때만 읽을 수 있어요.'}</li>`
    : ids.map(id => {
      const r = scanned.get(id), checked = d.disable_rules.includes(id), byUs = fw.disabled_by_us.includes(id);
      const name = r?.name || names[id] || id;
      return `<li><div class="grow">${esc(name)}${r?.suggested ? '<span class="tag">권장</span>' : ''}${byUs ? '<span class="tag good">svc.watch가 끔</span>' : ''}
        <span>${r ? `${esc(r.protocol || 'Any')} ${esc(r.ports || 'Any')} · ${esc(r.program && r.program !== 'Any' ? r.program : r.group || '모든 프로그램')} · ${esc(r.profile)}` : '현재 꺼져 있음'}</span></div>
        <span class="muted" style="font-size:13px">${checked ? '적용 시 끄기' : '그대로 둠'}</span>
        <label class="switch"><input type="checkbox" data-conf="${esc(id)}" data-name="${esc(name)}" ${checked ? 'checked' : ''} aria-label="${esc(name)} 적용 시 끄기"><span></span></label></li>`;
    }).join('') + (conf.error ? `<li class="form-err">${esc(conf.error)}</li>` : '')
    + (conf.scanned_at && !(conf.items || []).length ? `<li class="muted">모든 IP를 허용하는 규칙이 없어요.</li>` : '');
  $('#fwScan').innerHTML = `${ic('search')}${conf.scanned_at ? `다시 검사 · ${hhmm(conf.scanned_at)}` : '검사'}`;
  $('#fwScan').disabled = !fw.admin;
  $('#fwScan').title = fw.admin ? '' : '관리자 권한으로 실행해야 검사할 수 있어요';

  renderApplyBar();
}

function renderApplyBar() {
  const fw = state.fw, d = state.fwDraft, bar = $('#applyBar'); if (!fw || !d) return;
  bar.className = 'applybar glass';
  if (fw.pending) {
    const left = Math.max(0, Math.round(fw.pending.deadline - Date.now() / 1000));
    bar.classList.add('rollback');
    bar.innerHTML = `${ic('undo', 'i')}<div style="flex:1"><b>새 규칙이 적용됐어요.</b> 접속이 정상이면 <b>유지</b>를 누르세요. 누르지 않으면 <span class="count-big">${left}</span>초 뒤 이전 설정으로 자동 복구됩니다.</div>
      <button class="btn" id="fwRevert">${ic('undo')}지금 되돌리기</button><button class="btn accent" id="fwKeep">유지</button>`;
    $('#fwRevert').onclick = () => withBusy($('#fwRevert'), async () => { await api('/api/firewall/rollback', { method: 'POST' }); toast('이전 설정으로 되돌렸어요'); await loadFirewall(); });
    $('#fwKeep').onclick = () => withBusy($('#fwKeep'), async () => { await api('/api/firewall/confirm', { method: 'POST' }); toast('새 규칙을 유지합니다'); await loadFirewall(); });
    return;
  }
  const dirty = fwKey(d) !== fwKey(fw.applied || { mode: false, allow: [], exposure: [], disable_rules: [] });
  if (dirty) {
    bar.classList.add('pending');
    bar.innerHTML = `${ic('alert', 'i')}<div style="flex:1">적용하지 않은 변경이 있어요</div>
      <label class="check"><input type="checkbox" id="fwAuto" ${state.fwAuto !== false ? 'checked' : ''}> 60초 안에 확인하지 않으면 자동 복구</label>
      <button class="btn" id="fwCancel">변경 취소</button>
      <button class="btn accent" id="fwApply" ${fw.admin ? '' : 'disabled title="관리자 권한으로 실행해야 적용할 수 있어요"'}>${ic('shield')}적용</button>`;
    $('#fwAuto').onchange = e => { state.fwAuto = e.target.checked; };
    $('#fwCancel').onclick = async () => {
      state.fwDraft = structuredClone(fw.applied || { ...d, mode: false, allow: [], exposure: [], disable_rules: [] });
      state.fwDraft.next_id = d.next_id;
      if (await saveDraft()) toast('적용된 상태로 되돌렸어요');
    };
    $('#fwApply').onclick = applyFirewall;
  } else {
    const lr = fw.last_rollback;
    bar.innerHTML = `${sIcon('running')}<div style="flex:1">${fw.applied_at ? `적용된 상태와 같아요 <span class="muted">· 마지막 적용 ${hhmm(fw.applied_at)}</span>` : '아직 적용한 적이 없어요'}${lr && Date.now() / 1000 - lr.at < 600 ? ` <span class="muted">· ${hhmm(lr.at)}에 ${lr.reason === 'timeout' ? '확인이 없어 자동 복구됨' : '되돌림'}</span>` : ''}</div>`;
  }
}

async function applyFirewall() {
  const auto = state.fwAuto !== false;
  const msg = `방화벽 규칙을 적용할까요?\n\n· 적용 전에 현재 방화벽 설정 전체를 백업합니다.\n${auto ? '· 60초 안에 [유지]를 누르지 않으면 자동으로 원래대로 돌아갑니다.' : '· 자동 복구가 꺼져 있어요. 문제가 생기면 직접 되돌려야 합니다.'}`;
  if (!confirm(msg)) return;
  await withBusy($('#fwApply'), async () => {
    await api('/api/firewall/apply', { method: 'POST', body: { auto_rollback: auto } });
    toast('적용했어요');
    await loadFirewall();
  });
}

function fwMutate(fn) { fn(state.fwDraft); saveDraft(); }

function parsePorts(text) {
  const ports = text.split(/[\s,]+/).filter(Boolean).map(Number);
  if (!ports.length || ports.some(p => !Number.isInteger(p) || p < 1 || p > 65535)) return null;
  return [...new Set(ports)];
}

$('#fwPorts').onchange = () => { $('#fwPortsCustom').classList.toggle('hidden-input', $('#fwPorts').value !== 'custom'); };
$('#fwAdd').onsubmit = async e => {
  e.preventDefault();
  const ip = $('#fwIp').value.trim(), msg = $('#fwIpMsg');
  const setErr = t => { $('#fwIp').setAttribute('aria-invalid', 'true'); msg.classList.add('err'); msg.textContent = t; $('#fwIp').focus(); };
  if (!ip) return setErr('IP 주소를 입력하세요');
  let ports = 'all';
  if ($('#fwPorts').value === 'custom') {
    ports = parsePorts($('#fwPortsCustom').value);
    if (!ports) { toast('포트는 1~65535 사이 숫자를 쉼표로 구분해 입력하세요', 'error'); $('#fwPortsCustom').focus(); return; }
  } else if ($('#fwPorts').value !== 'all') ports = [+$('#fwPorts').value];
  const before = structuredClone(state.fwDraft);
  state.fwDraft.allow.push({ id: state.fwDraft.next_id++, on: true, name: $('#fwName').value.trim() || ip, cidr: ip, ports, expires: null });
  try {
    state.fwDraft = await api('/api/firewall/config', { method: 'PUT', body: state.fwDraft });
    state.fw.config = structuredClone(state.fwDraft);
    $('#fwIp').value = ''; $('#fwName').value = ''; $('#fwIp').removeAttribute('aria-invalid');
    msg.classList.remove('err'); msg.textContent = 'IP 하나 또는 CIDR 대역 (IPv6도 가능)';
    if (!isPrivateIp(ip)) toast('인터넷 IP를 허용했어요. 꼭 필요한 포트만 열어 두세요');
    renderFirewall(); loadPreview();
  } catch (err) { state.fwDraft = before; setErr(err.message); }
};
$('#fwIp').oninput = () => { $('#fwIp').removeAttribute('aria-invalid'); $('#fwIpMsg').classList.remove('err'); $('#fwIpMsg').textContent = 'IP 하나 또는 CIDR 대역 (IPv6도 가능)'; };
$('#fwExpoAdd').onsubmit = e => {
  e.preventDefault();
  const port = +$('#fwExpoPort').value;
  if (!Number.isInteger(port) || port < 1 || port > 65535) { toast('1~65535 사이 포트 번호를 입력하세요', 'error'); $('#fwExpoPort').focus(); return; }
  if (state.fwDraft.exposure.some(x => x.port === port)) { toast('이미 목록에 있는 포트예요', 'error'); return; }
  fwMutate(d => d.exposure.push({ port, label: $('#fwExpoLabel').value.trim(), mode: 'local' }));
  $('#fwExpoPort').value = ''; $('#fwExpoLabel').value = '';
};
$('#view-firewall').addEventListener('click', e => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.dataset.del) fwMutate(d => { d.allow = d.allow.filter(a => a.id !== +t.dataset.del); });
  else if (t.dataset.expo) fwMutate(d => { d.exposure[+t.dataset.expo].mode = t.dataset.m; });
  else if (t.dataset.expodel) fwMutate(d => { d.exposure.splice(+t.dataset.expodel, 1); });
  else if (t.dataset.sugg) fwMutate(d => d.exposure.push({ port: +t.dataset.sugg, label: t.dataset.label, mode: 'local' }));
  else if (t.dataset.allowip) { $('#fwIp').value = t.dataset.allowip; $('#fwIp').scrollIntoView({ block: 'center' }); $('#fwName').focus(); }
});
$('#view-firewall').addEventListener('change', e => {
  const t = e.target;
  if (t.id === 'fwMode') fwMutate(d => { d.mode = t.checked; });
  else if (t.dataset.tog) fwMutate(d => { d.allow.find(a => a.id === +t.dataset.tog).on = t.checked; });
  else if (t.dataset.exp) fwMutate(d => { d.allow.find(a => a.id === +t.dataset.exp).expires = t.value || null; });
  else if (t.dataset.conf) fwMutate(d => {
    const id = t.dataset.conf;
    d.disable_rules = d.disable_rules.filter(x => x !== id);
    d.rule_names = d.rule_names || {};
    if (t.checked) { d.disable_rules.push(id); d.rule_names[id] = t.dataset.name; }
  });
});
$('#fwScan').onclick = () => withBusy($('#fwScan'), async () => {
  $('#fwScan').innerHTML = `${ic('search')}검사 중…`;
  state.fw.conflicts = await api('/api/firewall/conflicts/scan', { method: 'POST' });
  const sugg = state.fw.conflicts.items.filter(r => r.suggested && !state.fwDraft.disable_rules.includes(r.id));
  renderFirewall();
  toast(`모든 IP를 허용하는 규칙 ${state.fw.conflicts.items.length}개를 찾았어요${sugg.length ? ` (끄기 권장 ${sugg.length}개)` : ''}`);
});
$('#fwCopy').onclick = () => { navigator.clipboard?.writeText($('#fwCode').textContent); toast('명령을 복사했어요 (긴 주소 목록은 줄여서 표시됨)'); };

/* ============================================================ views & wiring */
const VIEWS = {
  dashboard: ['서비스 현황', '이 컴퓨터에 등록한 서비스의 상태를 실시간으로 확인합니다.'],
  docker: ['Docker 컨테이너', 'Docker Desktop에서 돌고 있는 컨테이너의 상태와 리소스 사용량입니다.'],
  firewall: ['방화벽 · 접속 허용 목록', '인터넷에서 들어오는 연결을 막고, 내가 허용한 IP만 들어올 수 있게 관리합니다.'],
};
function setView(v) {
  state.view = v;
  for (const k in VIEWS) $('#view-' + k).hidden = k !== v;
  $$('nav a').forEach(a => a.dataset.view === v ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'));
  $('#pageTitle').textContent = VIEWS[v][0]; $('#pageSub').textContent = VIEWS[v][1];
  $('#addBtn').hidden = v === 'firewall';
  $('#live').hidden = $('#pauseBtn').hidden = v === 'firewall';
  if (location.hash.slice(1) !== v) history.replaceState(null, '', '#' + v);
  if (v === 'docker') loadDocker();
  if (v === 'firewall') loadFirewall();
}
$$('nav a').forEach(a => a.onclick = e => { e.preventDefault(); setView(a.dataset.view); });
window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (VIEWS[v] && v !== state.view) setView(v); });

$('#addBtn').onclick = () => openModal(state.view === 'docker' ? { type: 'docker' } : undefined);
$('#cancelModal').onclick = closeModal; $('#xModal').onclick = closeModal;
$('#modal').onclick = e => { if (e.target.id === 'modal') closeModal(); };
$('#closeDrawer').onclick = closeDrawer; $('#scrim').onclick = closeDrawer;
$$('#dTabs .tab').forEach(t => t.onclick = () => { state.tab = t.dataset.tab; refreshDrawer(); });
$('#q').oninput = e => { state.q = e.target.value; renderRows(); };
$('#typeSel').onchange = e => { state.type = e.target.value; renderRows(); };
$('#statusSeg').onclick = e => { const b = e.target.closest('button'); if (b) { state.status = b.dataset.k; renderSeg(); renderRows(); } };
$('#dq').oninput = e => { state.dq = e.target.value; renderDkRows(); };
$('#dkGroup').onchange = e => { state.dgroup = e.target.checked; renderDkRows(); };
$('#dkSeg').onclick = e => { const b = e.target.closest('button'); if (b) { state.df = b.dataset.k; renderDocker(); } };
$('#dkPrune').onclick = () => {
  if (!confirm('사용하지 않는 Docker 데이터를 정리할까요?\n\n중지된 컨테이너, 사용하지 않는 네트워크, 태그 없는 이미지, 빌드 캐시가 삭제됩니다. (볼륨은 지우지 않아요)')) return;
  withBusy($('#dkPrune'), async () => { const r = await api('/api/docker/prune', { method: 'POST' }); toast(r.message || '정리 완료'); loadDocker(); });
};
$('#notifyBtn').onclick = async () => { await Notification.requestPermission(); $('#notifyBtn').hidden = Notification.permission !== 'default'; };

document.addEventListener('click', e => {
  if (e.target.closest('a[href]')) return;  // web links open in a new tab without opening the drawer
  const of = e.target.closest('[data-openfolder]');
  if (of) {
    withBusy(of, async () => { const r = await api(`/api/services/${of.dataset.openfolder}/open-folder`, { method: 'POST' }); toast(`파일 탐색기에서 열었어요 · ${r.path}`); });
    return;
  }
  if (e.target.closest('[data-add]')) { openModal(); return; }
  const act = e.target.closest('#rows [data-act]');
  if (act) { controlService(+act.closest('tr').dataset.id, act.dataset.act, act); return; }
  const row = e.target.closest('#rows tr[data-id]');
  if (row) { openService(+row.dataset.id); return; }
  const cact = e.target.closest('#dkRows [data-cact]');
  if (cact) { controlContainer(cact.closest('tr').dataset.cname, cact.dataset.cact, cact); return; }
  const crow = e.target.closest('#dkRows tr[data-cname]');
  if (crow) { openContainer(crow.dataset.cname); return; }
  const dact = e.target.closest('[data-dact]');
  if (dact && state.drawer?.kind === 'svc') { controlService(state.drawer.id, dact.dataset.dact, dact).then(() => setTimeout(refreshDrawer, 1500)); return; }
  const dcact = e.target.closest('[data-dcact]');
  if (dcact && state.drawer?.kind === 'ctr') controlContainer(state.drawer.name, dcact.dataset.dcact, dcact);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.matches('#rows tr[data-id]')) openService(+e.target.dataset.id);
  if (e.key === 'Enter' && e.target.matches('#dkRows tr[data-cname]')) openContainer(e.target.dataset.cname);
  if (e.key !== 'Escape') return;
  if ($('#modal').classList.contains('open')) closeModal();
  else if (state.drawer) closeDrawer();
});

$('#themeBtn').onclick = () => {
  const toLight = document.documentElement.dataset.theme === 'dark';
  document.documentElement.dataset.theme = toLight ? 'light' : 'dark';
  localStorage.setItem('theme', toLight ? 'light' : 'dark');
  syncThemeBtn();
  renderDashboard(); if (state.view === 'docker') renderDocker();
};
function syncThemeBtn() {
  const dark = document.documentElement.dataset.theme === 'dark';
  $('#themeBtn').innerHTML = ic(dark ? 'sun' : 'moon', 'i');
  $('#themeBtn').setAttribute('aria-label', dark ? '라이트 모드로 전환' : '다크 모드로 전환');
}
$('#pauseBtn').onclick = () => {
  state.paused = !state.paused;
  $('#pauseBtn').setAttribute('aria-pressed', state.paused);
  $('#pauseBtn').innerHTML = state.paused ? `${ic('play')}<span>재개</span>` : `${ic('pause')}<span>일시정지</span>`;
  renderLive();
};

/* Polling: overview every 5s (needed for nav badges), plus the visible view. Stops while hidden or paused. */
let tick = 0;
setInterval(() => {
  tick++;
  if (document.hidden) return;
  if (state.fw?.pending && state.view === 'firewall') {
    renderApplyBar();
    if (tick % 3 === 0) loadFirewall(true);
  }
  if (state.paused || tick % 5) return;
  loadOverview();
  if (state.view === 'docker') loadDocker();
  if (state.view === 'firewall' && tick % 30 === 0 && !state.fw?.pending) loadFirewall(true);
}, 1000);

syncThemeBtn();
loadOverview();
setView(VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : 'dashboard');
