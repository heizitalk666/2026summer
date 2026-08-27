const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const api = (p, o) => fetch(p, o).then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t) }));
const fmtTime = ms => new Date(ms).toLocaleString('zh-CN', { hour12: false });
const pill = (cls, txt) => `<span class="pill ${cls}">${txt}</span>`;

$$('.tab').forEach(b => b.onclick = () => {
  $$('.tab').forEach(x => x.classList.toggle('active', x === b));
  $$('.panel').forEach(p => p.classList.toggle('hidden', p.id !== b.dataset.tab));
  ({ ledger: loadLedger, review: loadReview, stats: loadStats, models: loadModels, live: loadLive })[b.dataset.tab]();
});
$('#closeDetail').onclick = () => $('#detail').classList.add('hidden');
$('#refresh').onclick = loadLedger;
$('#runFilter').onchange = loadLedger;
$('#verdictFilter').onchange = loadLedger;

async function loadRuns() {
  const runs = await api('/api/runs');
  $('#runFilter').innerHTML = '<option value="">全部</option>' +
    runs.map(r => `<option value="${r.run_id}">${r.run_id}（${r.n} 条）</option>`).join('');
}

async function loadLedger() {
  const q = new URLSearchParams();
  if ($('#runFilter').value) q.set('run_id', $('#runFilter').value);
  if ($('#verdictFilter').value) q.set('verdict', $('#verdictFilter').value);
  const rows = await api('/api/evidence?' + q);
  $('#tbl tbody').innerHTML = rows.map(r => `<tr>
    <td>${fmtTime(r.ts_utc_ms)}</td><td>${r.waypoint_id ?? '—'}</td>
    <td>${r.defect_class ?? '—'}</td>
    <td>${pill('v-' + r.verdict, r.verdict)}</td>
    <td>${pill('s-' + r.severity, r.severity)}</td>
    <td class="num">${(r.delta_conf ?? 0).toFixed(3)}</td>
    <td class="num">${(r.density_ratio ?? 0).toFixed(2)}</td>
    <td>${r.needs_review ? '待复核' : (r.aborted ? '中止' : '已闭环')}</td>
    <td><button data-ev="${r.event_id}">详情</button></td></tr>`).join('')
    || '<tr><td colspan="9">暂无数据。跑一轮 patrol.tools.run_all 就会有了。</td></tr>';
  $$('#tbl button').forEach(b => b.onclick = () => showDetail(b.dataset.ev));
  const pending = await api('/api/evidence?needs_review=true');
  $('#badge').textContent = pending.length;
}

async function showDetail(id) {
  const d = await api('/api/evidence/' + encodeURIComponent(id));
  const m = d.manifest, g = m.gain || {}, b = m.before || {}, a = m.after || {};
  const imgs = (d.assets || []).filter(x => /\.(jpg|png)$/i.test(x.name));
  $('#detailBody').innerHTML = `
    <h2>${m.event_id.slice(0, 8)} · ${m.waypoint_id}</h2>
    <dl>
      <dt>结论</dt><dd>${pill('v-' + m.verdict.result, m.verdict.result)} ${pill('s-' + m.verdict.severity, m.verdict.severity)}</dd>
      <dt>run_id</dt><dd>${m.run_id}</dd>
      <dt>时间</dt><dd>${fmtTime(m.ts_utc_ms)}</dd>
      <dt>Δconf</dt><dd>${(g.delta_conf ?? 0).toFixed(3)}（${b.confidence?.toFixed(2)} → ${a.confidence?.toFixed(2)}）</dd>
      <dt>像素密度比</dt><dd>${(g.pixel_density_ratio ?? 0).toFixed(2)}（${b.pixel_density_px?.toFixed(0)} → ${a.pixel_density_px?.toFixed(0)} px）</dd>
      <dt>变焦</dt><dd>${b.zoom?.toFixed(1)}× → ${a.zoom?.toFixed(1)}×</dd>
      <dt>复核成功</dt><dd>${g.verify_success ? '是' : '否'}</dd>
      ${a.l2_reading ? `<dt>读数</dt><dd>${a.l2_reading.value ?? '—'} ${a.l2_reading.unit ?? ''}
        ${a.l2_reading.in_normal_band === false ? '（超出正常区间）' : ''}</dd>` : ''}
      ${m.abort ? `<dt>中止</dt><dd>${m.abort.at_state} · ${m.abort.reason}<br><small>${m.abort.detail ?? ''}</small></dd>` : ''}
    </dl>
    ${imgs.length ? `<div class="gallery">${imgs.map(x =>
      `<figure><img loading="lazy" src="/api/files/${m.run_id}/${m.event_id}/${x.name}">
       <figcaption>${x.name}</figcaption></figure>`).join('')}</div>` : ''}
    <h2>状态机耗时</h2>
    <div class="timeline">${(m.timeline || []).map(t =>
      `<div><span>${t.state}</span><span>${t.duration_ms} ms</span></div>`).join('')}
      <div><strong>合计</strong><strong>${(m.timeline || []).reduce((s, t) => s + t.duration_ms, 0)} ms</strong></div></div>
    <h2>人工复核</h2>
    <div class="reviewCard">
      <input id="rv" placeholder="复核人"><input id="rn" placeholder="意见" style="width:100%;margin-top:6px">
      <div class="acts">
        ${['DEFECT', 'FALSE_ALARM', 'CONFIRM', 'NEED_MORE'].map(x =>
          `<button data-dec="${x}">${{DEFECT:'判为缺陷',FALSE_ALARM:'判为误报',CONFIRM:'确认 AI 结论',NEED_MORE:'需补拍'}[x]}</button>`).join('')}
      </div>
      <div class="log">${(d.reviews || []).map(r =>
        `${fmtTime(r.ts_utc_ms)} · ${r.reviewer} · ${r.decision}${r.note ? ' · ' + r.note : ''}`).join('<br>') || '尚无复核记录'}</div>
    </div>`;
  $$('#detailBody .acts button').forEach(btn => btn.onclick = async () => {
    await api(`/api/evidence/${encodeURIComponent(id)}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reviewer: $('#rv').value || '匿名', decision: btn.dataset.dec, note: $('#rn').value })
    });
    showDetail(id); loadLedger();
  });
  $('#detail').classList.remove('hidden');
}

async function loadReview() {
  const rows = await api('/api/evidence?needs_review=true');
  $('#reviewList').innerHTML = rows.map(r => `<div class="reviewCard">
    <strong>${r.waypoint_id ?? '—'}</strong> · ${pill('v-' + r.verdict, r.verdict)}
    · ${fmtTime(r.ts_utc_ms)}<br>
    <span class="log">${r.defect_class ?? '未分类'} · 置信 ${(r.confidence ?? 0).toFixed(2)}
      ${r.aborted ? '· 中止于 ' + (r.abort_reason ?? '') : ''}</span>
    <div class="acts"><button data-ev="${r.event_id}">打开详情</button></div></div>`).join('')
    || '<p class="hint">没有待复核事项。</p>';
  $$('#reviewList button').forEach(b => b.onclick = () => showDetail(b.dataset.ev));
}

async function loadStats() {
  const s = await api('/api/stats' + ($('#runFilter').value ? '?run_id=' + $('#runFilter').value : ''));
  const targets = { d: 0.25, r: 2.2, ok: 0.85 };
  const dc = s.delta_conf_on_real_defects;
  $('#statCards').innerHTML = `
    <div class="card"><dt>Δconf（真缺陷组）</dt><dd>${dc === null ? '—' : dc.toFixed(3)}</dd>
      <div class="note">目标 &gt; +${targets.d} ${dc !== null ? (dc > targets.d ? '· 达标' : '· 未达标') : ''}</div></div>
    <div class="card"><dt>复核成功率</dt><dd>${(s.verify_success_rate * 100).toFixed(1)}%</dd>
      <div class="note">目标 &gt; ${targets.ok * 100}%</div></div>
    <div class="card"><dt>证据包总数</dt><dd>${s.total}</dd></div>`;
  $('#statTbl tbody').innerHTML = Object.entries(s.by_verdict).map(([k, v]) =>
    `<tr><td>${pill('v-' + k, k)}</td><td class="num">${v.n}</td>
     <td class="num">${v.avg_delta_conf.toFixed(3)}</td>
     <td class="num">${v.avg_density_ratio.toFixed(2)}</td>
     <td class="num">${v.verify_ok}</td></tr>`).join('')
    || '<tr><td colspan="5">暂无数据</td></tr>';
}

async function loadModels() {
  const rows = await api('/api/models');
  $('#modelTbl tbody').innerHTML = rows.map(m => `<tr>
    <td>${m.version}</td><td>${m.stage}</td><td>${m.dataset ?? '—'}</td>
    <td class="log">${m.metrics ?? ''}</td><td>${m.note ?? ''}</td>
    <td>${m.active ? pill('s-INFO', '当前') : ''}</td></tr>`).join('')
    || '<tr><td colspan="6">尚未登记模型版本</td></tr>';
}
$('#modelForm').onsubmit = async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  await api('/api/models', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      version: f.get('version'), stage: f.get('stage'), dataset: f.get('dataset'),
      note: f.get('note'), activate: f.get('activate') === 'on'
    })
  });
  e.target.reset(); loadModels();
};

loadRuns().then(loadLedger);

/* ==================================================================
   实时页：指令流水 + 配电室俯视
   ------------------------------------------------------------------
   数据来自 patrol/tools/console.py 推上来的 /api/live/push，云端只在
   内存里留最近一段，不落库。所以刷新页面会丢历史——这是有意的：实时页
   要看的是"现在在做什么"，事后要查的东西在台账页。
   ================================================================== */
const LIVE = { after: 0, timer: null, map: null, rows: [] };

async function loadLive() {
  if (!LIVE.map) {
    try { LIVE.map = await api('/api/live/map'); } catch (e) { LIVE.map = { waypoints: [], targets: [] }; }
    drawMapBase();
  }
  if (!LIVE.timer) LIVE.timer = setInterval(pollLive, 500);
  pollLive();
}
function stopLive() { clearInterval(LIVE.timer); LIVE.timer = null; }

async function pollLive() {
  if ($('#live').classList.contains('hidden')) return stopLive();
  let s;
  try { s = await api(`/api/live/state?after_ms=${LIVE.after}`); }
  catch (e) { return setLiveState('off', '云端不可达'); }

  const stale = s.stale_s;
  if (stale === null) setLiveState('off', '等待车端数据…（先跑 python -m patrol.tools.console --push http://127.0.0.1:8000）');
  else if (stale > 5) setLiveState('stale', `已 ${stale.toFixed(0)} s 没有新数据`);
  else setLiveState('', '车端在线');

  const showHb = $('#liveHeartbeat').checked;
  for (const c of s.commands) {
    LIVE.after = Math.max(LIVE.after, c.ts_utc_ms || 0);
    if (c.command === 'HEARTBEAT' && c.ok && !showHb) continue;
    LIVE.rows.push(c);
  }
  if (LIVE.rows.length > 300) LIVE.rows = LIVE.rows.slice(-300);
  renderLiveRows();
  if (s.snapshot && Object.keys(s.snapshot).length) drawMapLive(s.snapshot);
}

function setLiveState(cls, text) {
  $('#liveDot').className = 'dot ' + cls;
  $('#liveState').textContent = text;
}

function renderLiveRows() {
  const t0 = LIVE.rows.length ? LIVE.rows[0].ts_utc_ms : 0;
  // 通过/拒绝并进"指令"那一列：单独一列会把耗时挤出可视范围，而耗时是
  // 演示时要说的数（每条指令 1–2 ms 就走完了整条校验链）
  $('#liveTbl tbody').innerHTML = LIVE.rows.map(c => `
    <tr class="${c.ok ? '' : 'bad'}">
      <td class="t">${((c.ts_utc_ms - t0) / 1000).toFixed(1)}s</td>
      <td class="tgt">${c.target || ''}</td>
      <td>${c.ok ? '✓' : '✗'} ${c.text || ''}${c.detail ? ' — ' + c.detail : ''}</td>
      <td class="t">${(c.latency_ms ?? 0).toFixed(1)} ms</td>
    </tr>`).join('');
  const box = $('.live-log');
  if (box) box.scrollTop = box.scrollHeight;   // 跟着最新的走
}

/* 俯视图。坐标直接用米，viewBox 就是配电室的平面尺寸，
   所以下面所有数字都能对着 configs/waypoints.yaml 读。
   注意 SVG 的 y 轴向下，而地图系 y 轴向上，所以画的时候取负。 */
function drawMapBase() {
  const wps = LIVE.map.waypoints || [], tg = LIVE.map.targets || [];
  const parts = [
    `<rect class="cab" x="-0.5" y="${-(1.82 + 0.9)}" width="17" height="1.2"/>`,
    `<polyline class="aisle" points="${wps.map(w => `${w.x_m},${-w.y_m}`).join(' ')}"/>`
  ];
  for (const w of wps) parts.push(
    `<circle class="wp" cx="${w.x_m}" cy="${-w.y_m}" r="0.12"/>`,
    `<text class="wplbl" x="${w.x_m}" y="${-w.y_m + 0.5}" text-anchor="middle">${(w.id || '').replace('WP-', '')}</text>`);
  for (const t of tg) parts.push(
    `<rect class="tgt" x="${t.x_m - 0.16}" y="${-t.y_m - 0.16}" width="0.32" height="0.32"/>`);
  $('#liveMap').innerHTML = parts.join('') + '<g id="liveCar"></g>';
}

function drawMapLive(s) {
  const x = s.x_m ?? 0, y = -(s.y_m ?? 0);
  // 相机方位角 = 车头朝向 + 云台 pan。视场半角由 hfov 给，它随变焦收窄——
  // 扇形一收紧就是"正在放大细看"，这是俯视图上最直观的一个信号。
  const az = ((s.yaw_deg ?? 0) + (s.pan_deg ?? 0)) * Math.PI / 180;
  const half = ((s.hfov_deg ?? 60) / 2) * Math.PI / 180;
  const R = 6.5;
  const p = (a) => `${(x + R * Math.cos(a)).toFixed(3)},${(y - R * Math.sin(a)).toFixed(3)}`;
  const head = `${(x + 0.55 * Math.cos((s.yaw_deg ?? 0) * Math.PI / 180)).toFixed(3)},${(y - 0.55 * Math.sin((s.yaw_deg ?? 0) * Math.PI / 180)).toFixed(3)}`;
  $('#liveCar').innerHTML =
    `<polygon class="fov" points="${x},${y} ${p(az - half)} ${p(az)} ${p(az + half)}"/>` +
    `<line class="carhead" x1="${x}" y1="${y}" x2="${head.split(',')[0]}" y2="${head.split(',')[1]}"/>` +
    `<circle class="car" cx="${x}" cy="${y}" r="0.20"/>`;
  $('#liveDet tbody').innerHTML = (s.detections || []).map(d => `
    <tr><td>${d.defect_class ?? ''}</td><td>${(d.confidence ?? 0).toFixed(2)}</td>
    <td>${(d.pixel_density_px ?? 0).toFixed(0)} px</td>
    <td>${d.l2 === null || d.l2 === undefined ? '—'
      : `${d.l2}${d.unit ? ' ' + d.unit : ''} ${d.in_band === false ? '出带' : d.in_band === true ? '带内' : ''}`}</td></tr>`).join('');
}

$('#liveClear').onclick = () => { LIVE.rows = []; renderLiveRows(); };
