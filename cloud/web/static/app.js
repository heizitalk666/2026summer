const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const api = (p, o) => fetch(p, o).then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t) }));
const fmtTime = ms => new Date(ms).toLocaleString('zh-CN', { hour12: false });
const pill = (cls, txt) => `<span class="pill ${cls}">${txt}</span>`;

$$('.tab').forEach(b => b.onclick = () => {
  $$('.tab').forEach(x => x.classList.toggle('active', x === b));
  $$('.panel').forEach(p => p.classList.toggle('hidden', p.id !== b.dataset.tab));
  ({ ledger: loadLedger, review: loadReview, stats: loadStats, models: loadModels })[b.dataset.tab]();
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
