const $ = s => document.querySelector(s);

// 把文本放进 HTML 前先转义，避免名字里有特殊字符时把页面弄乱
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));

const fmtDur = s => s==null ? '–' :
  s<60     ? `${s|0}s` :
  s<3600   ? `${(s/60)|0}m${String((s%60)|0).padStart(2,'0')}s` :
  s<86400  ? `${(s/3600)|0}h${String(((s%3600)/60)|0).padStart(2,'0')}m` :
             `${(s/86400)|0}d${String(((s%86400)/3600)|0).padStart(2,'0')}h`;

const fmtTime = (ts, tz) => ts==null ? '–' :
  new Date(ts*1000).toLocaleString('zh-CN', { timeZone: tz,
    month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' });

const ago = ts => ts==null ? '–' : fmtDur(Math.max(0, Math.floor(Date.now()/1000 - ts))) + '前';
const until = ts => ts==null ? '' : fmtDur(Math.max(0, Math.floor(ts - Date.now()/1000))) + '后';

const badge = phase => {
  const m = { Running:'b-run', Success:'b-ok', Failed:'b-fail', Pending:'b-idle' };
  return `<span class="badge ${m[phase]||'b-idle'}">${esc(phase)}</span>`;
};
const rateCls = r => r==null ? '' : r>=95 ? 'rate-good' : r>=80 ? 'rate-mid' : 'rate-bad';
const empty = (n, t='无') => `<tr><td class="empty" colspan="${n}">${t}</td></tr>`;
const debounce = (fn, ms=150) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

let DATA = null, GROUP = '', TAB = 'cj', tz = 'Asia/Shanghai';

// ---------- 卡片 ----------
function renderCards(d) {
  const c = d.counts;
  const card = (k, v, cls='') => `<div class="card"><div class="k">${k}</div><div class="v ${v ? cls : 'zero'}">${v}</div></div>`;
  $('#cards').innerHTML =
    card('任务总数', c.cronjobs, 'ok') +
    card('运行中 Pod', c.running_pods, 'ok') +
    card('活跃 Job', c.active_jobs) +
    card('上次失败的任务', c.last_failed, 'err') +
    card('调度异常', c.flagged, 'err') +
    card('未就绪 Pod', c.bad_pods, 'err') +
    card('常驻服务 异常/总数', `${c.services_bad}/${c.services_up}`, c.services_bad ? 'err' : 'ok') +
    card('挂起任务', c.suspended, 'warn') +
    card('近期失败 Job 数', c.failed_recent, 'warn');
  // 数据年龄在 refresh() 里写进 meta
}

// ---------- 分组 ----------
function renderGroups(d) {
  const gs = Object.entries(d.groups);
  $('#groups').innerHTML =
    `<span class="chip ${GROUP===''?'active':''}" data-g="">全部(${d.counts.cronjobs})</span>` +
    gs.map(([g,v]) => `<span class="chip ${GROUP===g?'active':''}" data-g="${esc(g)}">${esc(g)}(${v.total})</span>`).join('');
}

// ---------- CronJob ----------
const cjMatch = {
  '':          () => true,
  running:     c => c.active_jobs > 0 || c.active_pods > 0,
  failed:      c => c.last_job && c.last_job.phase === 'Failed',
  flagged:     c => !!c.flag,
  suspended:   c => c.suspend,
};

function renderCronjobs(d) {
  const q = ($('#q-cj').value || '').toLowerCase();
  const st = $('#st-cj').value;
  $('#tb-cj').innerHTML = d.cronjobs
    .filter(c => (!GROUP || c.group===GROUP) && c.name.toLowerCase().includes(q) && cjMatch[st](c))
    .map(c => {
      const lj = c.last_job;
      let stHtml = c.suspend ? '<span class="badge b-susp">Suspended</span>' : !lj ? '–' : badge(lj.phase);
      if (c.flag === 'blocked')
        stHtml += ' <span class="badge b-fail" title="Forbid 策略下有 Job 运行超过一个调度周期，后续调度被跳过">疑似阻塞</span>';
      if (c.flag === 'stale')
        stHtml += ' <span class="badge b-fail" title="超过 2 个调度周期没有产生新的 Job">调度超期</span>';
      const lf = c.last_fail;
      return `<tr>
        <td class="mono"><a class="link" data-cj="${esc(c.name)}" title="查看该任务的执行记录">${esc(c.name)}</a></td>
        <td class="mono">${esc(c.schedule)} <span class="badge b-idle">${esc(c.timezone)}</span></td>
        <td title="${esc(until(c.next_run))}">${fmtTime(c.next_run, tz)}</td>
        <td>${ago(c.last_schedule)}</td>
        <td>${lj ? fmtDur(lj.duration) : '–'}</td>
        <td class="${rateCls(c.success_rate)}">${c.success_rate==null?'–':c.success_rate+'%'}
            <span class="mono muted">(${c.recent_total})</span></td>
        <td>${stHtml}</td>
        <td>${c.active_pods ? `<span class="warn">${c.active_pods}</span>` : 0}</td>
        <td class="trunc">${lf ? `${ago(lf.time)} <span class="mono muted">${esc(lf.reason)}</span>` : '–'}</td>
      </tr>`;
    }).join('') || empty(9);
}

// ---------- Pod ----------
const logBtn = pod => (DATA && DATA.meta.logs_enabled && pod)
  ? `<button class="btn-log" data-pod="${esc(pod)}">日志</button>` : '';

function renderPods(d) {
  const q = ($('#q-pod').value || '').toLowerCase();
  const abn = $('#pod-abn').checked;
  $('#tb-pods').innerHTML = d.running_pods
    .filter(p => (!abn || !p.ready_ok) &&
      (`${p.name} ${p.workload} ${p.node||''} ${p.status}`).toLowerCase().includes(q))
    .map(p => {
      const okSt = p.status === 'Running' && p.ready_ok;
      const rs = p.restarts > 0
        ? `<span class="warn">${p.restarts}</span> <span class="mono muted">${esc(p.last_reason)} ${p.last_restart ? ago(p.last_restart) : ''}</span>`
        : '0';
      return `<tr>
        <td class="mono">${esc(p.name)}</td>
        <td class="mono muted">${esc(p.workload)} <span class="badge b-idle">${esc(p.kind)}</span></td>
        <td><span class="badge ${okSt ? 'b-ok' : 'b-fail'}">${esc(p.status)}</span></td>
        <td class="${p.ready_ok ? '' : 'warn'}">${esc(p.ready)}</td>
        <td>${fmtDur(p.age)}</td>
        <td>${rs}</td>
        <td class="mono muted">${esc(p.node||'–')}</td>
        <td>${logBtn(p.name)}</td>
      </tr>`;
    }).join('') || empty(8, abn ? '没有异常 Pod' : '暂无运行中 Pod');
}

// ---------- Job ----------
function renderJobs(d) {
  const q = ($('#q-job').value || '').toLowerCase();
  const pf = $('#phase-filter').value;
  $('#tb-jobs').innerHTML = d.recent_jobs
    .filter(j => (!pf || j.phase===pf) &&
                 (j.name.toLowerCase().includes(q) || (j.cronjob||'').toLowerCase().includes(q)))
    .slice(0, 300)
    .map(j => `<tr>
      <td class="mono">${esc(j.name)}</td>
      <td class="mono muted">${esc(j.cronjob||'–')}</td>
      <td>${badge(j.phase)}</td>
      <td>${fmtTime(j.start, tz)}</td>
      <td>${fmtTime(j.end, tz)}</td>
      <td>${fmtDur(j.duration)}</td>
      <td class="${j.failed_retries>0?'warn':''}">${j.failed_retries}</td>
      <td class="mono trunc" title="${esc(j.reason)}">${esc(j.reason)}</td>
      <td>${logBtn(j.pod)}</td>
    </tr>`).join('') || empty(9);
}

// ---------- 常驻服务 ----------
function renderWorkloads(d) {
  const w = d.workloads;
  const hide = $('#hide-zero').checked;
  const keep = x => !hide || x.desired_num > 0;
  const hidden = w.deployments.filter(x => !keep(x)).length + w.statefulsets.filter(x => !keep(x)).length;
  $('#zero-hint').textContent = hide && hidden ? `已隐藏 ${hidden} 个` : '';

  const img = x => `<td class="mono muted" style="font-size:11px" title="${esc(x.image_full)}">${esc(x.image)}</td>`;
  const rdy = x => `<td class="${x.ready_num === x.desired_num ? '' : 'warn'}">${esc(x.ready)}</td>`;

  $('#tb-deploy').innerHTML = w.deployments.filter(keep).map(x => `<tr>
      <td class="mono">${esc(x.name)}</td>${rdy(x)}${img(x)}
      <td>${fmtDur(x.age)}</td>
      <td>${x.pods.running}/${x.pods.total}
          ${x.pods.restarts>0?`<span class="warn">·重启${x.pods.restarts}</span>`:''}</td>
      <td>${x.desired_num === 0 ? '<span class="badge b-idle">Scaled to 0</span>'
          : x.available ? '<span class="badge b-ok">Available</span>'
                        : '<span class="badge b-fail">NotReady</span>'}</td>
    </tr>`).join('') || empty(6);

  const simple = list => list.filter(keep).map(x => `<tr>
      <td class="mono">${esc(x.name)}</td>${rdy(x)}${img(x)}
      <td>${fmtDur(x.age)}</td><td>${x.pods.running}/${x.pods.total}</td></tr>`).join('') || empty(5);
  $('#tb-sts').innerHTML = simple(w.statefulsets);
  $('#tb-ds').innerHTML  = simple(w.daemonsets);

  $('#tb-svc').innerHTML = w.services.map(s => `<tr>
      <td class="mono">${esc(s.name)}</td>
      <td><span class="badge b-idle">${esc(s.type)}</span></td>
      <td class="mono">${esc(s.cluster_ip)}</td>
      <td class="mono muted">${esc(s.external||'–')}</td>
      <td class="mono">${esc(s.ports)}</td>
      <td>${fmtDur(s.age)}</td></tr>`).join('') || empty(6);
}

// ---------- 渲染：只渲染当前看到的那个标签页 ----------
const RENDER = { cj: renderCronjobs, pods: renderPods, jobs: renderJobs, wl: renderWorkloads };

function renderTab() {
  if (!DATA) return;
  if (TAB === 'cj') renderGroups(DATA);
  RENDER[TAB](DATA);
}
function renderAll() {
  if (!DATA) return;
  renderCards(DATA);
  renderTab();
}

function setTab(t) {
  if (!RENDER[t]) t = 'cj';
  TAB = t;
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  for (const k of Object.keys(RENDER)) $('#tab-' + k).hidden = k !== t;
  history.replaceState(null, '', '#' + t);
  renderTab();
}

// ---------- 拉数据（关键：上一次没回来就不发下一次） ----------
let timer = null, inflight = false, lastOkAt = 0;

function schedule(ms = 10000) {
  clearTimeout(timer);
  if ($('#auto').checked && !document.hidden) timer = setTimeout(refresh, ms);
}

function showBanner(msg, isErr) {
  const b = $('#banner');
  b.hidden = !msg;
  b.textContent = msg || '';
  b.classList.toggle('err', !!isErr);
}

async function refresh() {
  if (inflight) return;                       // 防止请求堆积
  inflight = true;
  $('#refresh').disabled = true;
  const dot = $('#live-dot');
  const ctrl = new AbortController();
  const killer = setTimeout(() => ctrl.abort(), 20000);   // 20 秒还没回来就放弃这次
  let nextIn = 10000;
  try {
    const r = await fetch('/api/overview', { cache: 'no-store', signal: ctrl.signal });
    if (r.status === 503) { nextIn = 2000; showBanner('后端刚启动，正在生成第一份数据…', false); return; }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    DATA = await r.json();
    tz = DATA.meta.display_tz;
    $('#company').textContent = DATA.meta.company;
    $('#pipeline').textContent = DATA.meta.pipeline;
    document.title = `${DATA.meta.company} · ${DATA.meta.pipeline}`;
    lastOkAt = Date.now();

    const age = +r.headers.get('X-Snapshot-Age') || 0;
    const serr = r.headers.get('X-Snapshot-Error');
    dot.className = 'dot' + (serr ? ' err' : age > 30 ? ' warn' : '');
    showBanner(serr ? `后端刷新 k8s 数据失败（下面显示的是 ${age}s 前的数据）：${decodeURIComponent(serr)}` : '', true);
    $('#meta').textContent =
      `${DATA.meta.namespace} · 时区 ${DATA.meta.display_tz} · 数据 ${age}s 前 · 后端构建 ${DATA.meta.build_ms ?? '?'}ms`;
    renderAll();
  } catch (e) {
    dot.className = 'dot err';
    const why = e.name === 'AbortError' ? '请求超时(20s)' : e.message;
    showBanner(`获取数据失败：${why}` + (lastOkAt ? `（页面显示的是 ${Math.round((Date.now()-lastOkAt)/1000)}s 前的数据）` : ''), true);
    console.error(e);
  } finally {
    clearTimeout(killer);
    inflight = false;
    $('#refresh').disabled = false;
    schedule(nextIn);                         // 这次结束后才安排下一次
  }
}

// ---------- 日志弹窗 ----------
let logPod = null;
async function loadLogs(resetContainer) {
  const body = $('#log-body');
  body.textContent = '加载中…';
  const p = new URLSearchParams({ tail: $('#log-tail').value, previous: $('#log-prev').checked });
  const c = $('#log-container').value;
  if (c && !resetContainer) p.set('container', c);
  try {
    const r = await fetch(`/api/pods/${encodeURIComponent(logPod)}/logs?${p}`, { cache: 'no-store' });
    const j = await r.json();
    if (!r.ok) { body.textContent = '错误：' + (j.detail || r.status); return; }
    const sel = $('#log-container');
    sel.innerHTML = j.containers.map(n => `<option ${n===j.container?'selected':''}>${esc(n)}</option>`).join('');
    body.textContent = j.text || '（空）';
    body.scrollTop = body.scrollHeight;
  } catch (e) { body.textContent = '错误：' + e.message; }
}
function openLogs(pod) {
  logPod = pod;
  $('#log-title').textContent = pod;
  $('#log-container').innerHTML = '';
  $('#log-prev').checked = false;
  $('#logdlg').showModal();
  loadLogs(true);
}

// ---------- 事件 ----------
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => setTab(b.dataset.tab));

// 事件委托：表格每次重绘，所以点击事件挂在父元素上
document.addEventListener('click', e => {
  const chip = e.target.closest('#groups .chip');
  if (chip) { GROUP = chip.dataset.g; renderTab(); return; }
  const cj = e.target.closest('a[data-cj]');
  if (cj) { $('#q-job').value = cj.dataset.cj; $('#phase-filter').value = ''; setTab('jobs'); return; }
  const lg = e.target.closest('.btn-log');
  if (lg) openLogs(lg.dataset.pod);
});

const rerender = debounce(renderTab);
$('#q-cj').oninput = rerender;
$('#q-pod').oninput = rerender;
$('#q-job').oninput = rerender;
$('#st-cj').onchange = renderTab;
$('#pod-abn').onchange = renderTab;
$('#phase-filter').onchange = renderTab;
$('#hide-zero').onchange = renderTab;

$('#refresh').onclick = () => { clearTimeout(timer); refresh(); };
$('#auto').onchange = () => schedule(0);

// 标签页切到后台时暂停轮询，切回来立刻刷新一次
document.addEventListener('visibilitychange', () => {
  if (document.hidden) clearTimeout(timer);
  else if ($('#auto').checked) refresh();
});

$('#log-reload').onclick = () => loadLogs(false);
$('#log-container').onchange = () => loadLogs(false);
$('#log-tail').onchange = () => loadLogs(false);
$('#log-prev').onchange = () => loadLogs(false);
$('#log-close').onclick = () => $('#logdlg').close();

setTab(location.hash.slice(1) || 'cj');
refresh();
