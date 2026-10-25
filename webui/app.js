/* ══════════════════════════════════════════════════════════════
   糕糕校园网自动认证 · 前端逻辑
   无框架、无 CDN —— 离线可用，打包体积不受影响。
   ══════════════════════════════════════════════════════════════ */
(function () {
'use strict';

// ── 工具 ────────────────────────────────────────────────
const $  = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

const state = {
  state: 'unknown',
  deviceKinds: { 1: '手机 WIFI 使用中', 2: '电脑有线使用中', 3: '电脑 WIFI 使用中' },
  confirmResolve: null,
  busy: false,
};

// ── API ─────────────────────────────────────────────────
async function api(path, body) {
  const opt = { method: body === undefined ? 'GET' : 'POST' };
  opt.headers = { 'X-Auth-Token': token() };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  let data = {};
  try { data = await res.json(); } catch (_) { /* 空响应 */ }
  if (!res.ok) throw new Error(data.message || ('HTTP ' + res.status));
  return data;
}

function token() {
  const m = document.cookie.match(/(?:^|;\s*)gg_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

// ── Toast ───────────────────────────────────────────────
function toast(msg, kind) {
  const wrap = $('#toasts');
  const el = document.createElement('div');
  el.className = 'toast' + (kind ? ' ' + kind : '');
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 240);
  }, kind === 'err' ? 5200 : 3000);
}

// ── 确认弹层 ─────────────────────────────────────────────
function confirmBox(title, text) {
  return new Promise((resolve) => {
    $('#confirmTitle').textContent = title;
    $('#confirmText').textContent = text;
    $('#confirmModal').hidden = false;
    state.confirmResolve = resolve;
  });
}

function closeConfirm(result) {
  $('#confirmModal').hidden = true;
  if (state.confirmResolve) {
    state.confirmResolve(result);
    state.confirmResolve = null;
  }
}

// ── 状态渲染 ─────────────────────────────────────────────
const STATE_TEXT = {
  online: '已认证在线',
  working: '处理中',
  error: '出错了',
  idle: '空闲',
  unknown: '尚未认证',
};

function renderStatus(st, text, badge) {
  state.state = st || 'unknown';
  const pill = $('#statusPill');
  pill.dataset.state = state.state;
  $('#statusText').textContent = text || STATE_TEXT[state.state] || '未知';
  $('#statusBadge').textContent = badge || '';
  $('#heroRing').dataset.state = state.state;
}

function renderInfo(info) {
  if (!info) return;
  const ip = info.ip || '';
  $('#heroIp').textContent = ip || '—';
  // 网关只显示 host[:port]，完整 URL 太长会被截断
  const gw = info.gateway ? info.gateway.replace(/^https?:\/\//, '') : '';
  $('#mGateway').textContent = gw || '—';
  $('#mGateway').title = info.gateway || '';
  $('#mMac').textContent = info.mac || '—';
  $('#mSlot').textContent = info.slot_ip || '—';
  $('#mDevices').textContent = info.devices_brief || '—';

  // 标题与描述跟着状态走
  const title = $('#heroTitle'), desc = $('#heroDesc');
  if (state.state === 'online') {
    title.textContent = '已连上校园网';
    desc.textContent = ip ? '本机出口 ' + ip + '，一切正常。' : '认证成功。';
  } else if (state.state === 'working') {
    title.textContent = '正在认证…';
    desc.textContent = '稍等，正在和网关协商。';
  } else if (state.state === 'error') {
    title.textContent = '出问题了';
    desc.textContent = '看下面的日志找原因，或点「扫码登录」重来。';
  } else if (ip) {
    title.textContent = '已连上校园网';
    desc.textContent = '出口 ' + ip + '，尚未认证。';
  } else {
    title.textContent = '未连接校园网';
    desc.textContent = '连上校园网后点「立即认证」。';
  }

  // 设置面板回填（只在用户没在编辑时回填，避免打断输入）
  if (!focusInField('inHost')) {
    $('#inHost').value = info.host || '';
  }
  if (!focusInField('inPort')) {
    $('#inPort').value = info.port || '1110';
  }
  if (!focusInField('inPhone'))  $('#inPhone').value = $('#inPhone').dataset.v || '';
  // 手机号只回填掩码形式，不回显明文
  if (!focusInField('inMobile')) {
    $('#inMobile').placeholder = info.account_masked
      ? (info.account_masked + '（已保存）') : '云高手机号';
  }
  $('#inAutostart').checked = !!info.autostart;
  $('#inKick').checked = !!info.auto_kick_phone;
}

function focusInField(id) {
  const el = document.getElementById(id);
  return el && el === document.activeElement;
}

// ── 日志 ────────────────────────────────────────────────
function addLog(text) {
  const box = $('#log');
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const line = document.createElement('div');
  line.className = 'log-line';
  // 关键字着色
  let cls = '';
  if (/失败|错误|异常|无效|拒绝/.test(text)) cls = 'err';
  else if (/★|成功|通过|已保存/.test(text)) cls = 'ok';
  else if (/警告|失败但|稍后|重试|熔断/.test(text)) cls = 'warn';
  if (text.indexOf('★') >= 0) cls = 'star';
  line.className = 'log-line' + (cls ? ' ' + cls : '');
  line.textContent = text;
  box.appendChild(line);
  while (box.childElementCount > 600) box.removeChild(box.firstChild);
  if (atBottom) box.scrollTop = box.scrollHeight;
}

// ── 设备列表 ─────────────────────────────────────────────
function renderDevices(devices, err, ip) {
  const list = $('#devicesList');
  list.innerHTML = '';
  $('#devicesCard').hidden = false;

  if (err) {
    list.innerHTML = '<div class="empty" style="color:var(--err)">查询失败：' + escapeHtml(err) + '</div>';
    return;
  }
  if (!devices || !devices.length) {
    list.innerHTML = '<div class="empty">当前没有设备在线</div>';
    return;
  }
  devices.forEach((d) => {
    const dip = String(d.ip || '?');
    const kind = state.deviceKinds[d.type] || '未知设备';
    const mine = ip && dip === ip;
    const el = document.createElement('div');
    el.className = 'device ' + (mine ? 'mine' : 'other');
    el.innerHTML =
      '<span class="device-dot"></span>' +
      '<span class="device-info"><b>' + escapeHtml(kind) +
      (mine ? '<span class="me-tag">本机</span>' : '') +
      '</b><span>IP ' + escapeHtml(dip) + '</span></span>';
    if (!mine) {
      const btn = document.createElement('button');
      btn.className = 'btn btn-sm';
      btn.textContent = '下线这台';
      btn.onclick = () => kickDevice(dip);
      el.appendChild(btn);
    }
    list.appendChild(el);
  });
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ── 动作 ────────────────────────────────────────────────
async function doLogin() {
  try {
    await api('/api/login', { takeover: false });
    toast('正在认证…', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

async function doTakeover() {
  try {
    const prev = await api('/api/takeover-preview', {});
    if (!prev.ok) { toast(prev.message || '无法抢占', 'err'); return; }
    let msg;
    if (prev.others && prev.others.length) {
      msg = '即将下线以下设备并接管名额：\n\n' + prev.others.join('、') +
            '\n\n这些设备会立刻断网。确定继续吗？';
    } else {
      msg = '即将尝试接管名额。\n\n如果名额正被手机占用，手机会被踢下线。确定继续吗？';
    }
    if (!await confirmBox('确认抢占上线', msg)) {
      toast('已取消抢占');
      return;
    }
    await api('/api/takeover', {});
  } catch (e) { toast(e.message, 'err'); }
}

async function doQr() {
  $('#qrModal').hidden = false;
  $('#qrImg').hidden = true;
  $('#qrImg').src = '';
  $('#qrLoading').hidden = false;
  $('#qrState').textContent = '正在获取…';
  $('#qrState').dataset.status = 'loading';
  try {
    await api('/api/qr', {});
  } catch (e) {
    $('#qrState').textContent = e.message;
    $('#qrState').dataset.status = 'failed';
  }
}

async function closeQr() {
  $('#qrModal').hidden = true;
  try { await api('/api/qr/cancel', {}); } catch (_) {}
}

async function refreshQr() {
  $('#qrImg').hidden = true;
  $('#qrLoading').hidden = false;
  $('#qrState').textContent = '正在获取…';
  $('#qrState').dataset.status = 'loading';
  try { await api('/api/qr', {}); } catch (e) { toast(e.message, 'err'); }
}

async function showDevices() {
  $('#devicesCard').hidden = false;
  $('#devicesList').innerHTML = '<div class="empty">查询中…</div>';
  try { await api('/api/devices', {}); } catch (e) { toast(e.message, 'err'); }
}

async function kickDevice(ip) {
  try {
    await api('/api/device/kick', { ip: ip });
    toast('正在下线 ' + ip);
  } catch (e) { toast(e.message, 'err'); }
}

async function logoutSelf() {
  if (!await confirmBox('归还名额', '将下线本机，把名额还给手机（如果你在手机上用）。\n\n确定继续吗？')) {
    return;
  }
  try { await api('/api/logout', {}); } catch (e) { toast(e.message, 'err'); }
}

async function checkToken() {
  try { await api('/api/token/check', {}); } catch (e) { toast(e.message, 'err'); }
}

async function saveToken() {
  const raw = $('#inToken').value.trim();
  if (!raw) { toast('Token 为空', 'err'); return; }
  try {
    const r = await api('/api/token/save', { token: raw });
    if (r.ok) {
      $('#inToken').value = '';
      toast(r.message || 'Token 已保存', 'ok');
    } else { toast(r.message, 'err'); }
  } catch (e) { toast(e.message, 'err'); }
}

async function saveSettings() {
  const body = {
    host: $('#inHost').value.trim(),
    port: $('#inPort').value.trim(),
    phone_ip: $('#inPhone').value.trim(),
    autostart: $('#inAutostart').checked,
    auto_kick_phone: $('#inKick').checked,
  };
  try {
    const r = await api('/api/settings/save', body);
    if (r.ok) {
      toast(r.message || '设置已保存', 'ok');
      $('#hostHint').textContent = '不同学校主机/端口不同。只允许已备案的网关主机。';
      $('#hostHint').className = 'field-hint';
    } else {
      toast(r.message, 'err');
      $('#hostHint').textContent = r.message;
      $('#hostHint').className = 'field-hint err';
    }
  } catch (e) {
    toast(e.message, 'err');
    $('#hostHint').textContent = e.message;
    $('#hostHint').className = 'field-hint err';
  }
}

async function clearCredentials() {
  if (!await confirmBox('清除已保存凭据',
      '将删除本机保存的 Token、手机号与密码。\n\n' +
      '清除后需要重新扫码或重新填写账号密码才能自动认证。\n\n确定继续吗？')) {
    return;
  }
  try {
    const r = await api('/api/settings/clear', {});
    if (r.ok) {
      $('#inToken').value = '';
      $('#inMobile').value = '';
      $('#inPwd').value = '';
      toast(r.message || '已清除', 'ok');
    } else { toast(r.message, 'err'); }
  } catch (e) { toast(e.message, 'err'); }
}

async function saveAccount() {
  const mobile = $('#inMobile').value.trim();
  const pwd = $('#inPwd').value;
  if (!mobile || !pwd) { toast('手机号或密码为空', 'err'); return; }
  try {
    await api('/api/account/save', { mobile: mobile, password: pwd });
    $('#inPwd').value = '';
    toast('正在用账号密码登录…', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

// ── 事件绑定 ─────────────────────────────────────────────
const ACTIONS = {
  'login': doLogin,
  'qr': doQr,
  'takeover': doTakeover,
  'devices': showDevices,
  'logout': logoutSelf,
  'checktoken': checkToken,
  'token-save': saveToken,
  'settings-save': saveSettings,
  'settings-clear': clearCredentials,
  'account-save': saveAccount,
  'qr-refresh': refreshQr,
  'qr-close': closeQr,
  'devices-refresh': showDevices,
  'log-clear': () => { $('#log').innerHTML = ''; },
  'confirm-yes': () => closeConfirm(true),
  'confirm-no': () => closeConfirm(false),
};

document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-act]');
  if (!btn) return;
  const fn = ACTIONS[btn.dataset.act];
  if (fn) { ev.preventDefault(); fn(); }
});

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') {
    if (!$('#confirmModal').hidden) closeConfirm(false);
    else if (!$('#qrModal').hidden) closeQr();
  }
  if (ev.key === 'Enter' && ev.target.id === 'inToken') saveToken();
  if (ev.key === 'Enter' && ev.target.id === 'inPwd') saveAccount();
});

// 点遮罩关弹层
$('#qrModal').addEventListener('click', (ev) => { if (ev.target.id === 'qrModal') closeQr(); });
$('#confirmModal').addEventListener('click', (ev) => { if (ev.target.id === 'confirmModal') closeConfirm(false); });

// ── SSE 实时推送 ─────────────────────────────────────────
function connectSSE() {
  const es = new EventSource('/api/events?t=' + encodeURIComponent(token()));
  es.onmessage = (ev) => {
    let e;
    try { e = JSON.parse(ev.data); } catch (_) { return; }
    switch (e.kind) {
      case 'log':
        addLog(e.text);
        break;
      case 'status':
        renderStatus(e.state, e.text, e.badge);
        break;
      case 'info':
      case 'info_render':
        refreshState();
        break;
      case 'devices':
        renderDevices(e.devices, e.error, e.ip);
        break;
      case 'qr':
        $('#qrImg').src = '/api/qr.png?t=' + encodeURIComponent(token()) +
                         '&r=' + Date.now();
        $('#qrImg').hidden = false;
        $('#qrLoading').hidden = true;
        break;
      case 'qr_state':
        $('#qrState').textContent = e.message;
        $('#qrState').dataset.status = e.status;
        if (e.status === 'authorized') {
          setTimeout(closeQr, 1400);
          toast('扫码授权成功', 'ok');
        }
        break;
      case 'token_state':
        $('#tokenHint').textContent = e.text;
        $('#tokenHint').className = 'field-hint' +
          (/有效/.test(e.text) ? ' ok' : (/失效/.test(e.text) ? ' err' : ''));
        break;
    }
  };
  es.onerror = () => {
    // 断线重连（EventSource 自带重连，这里只更新提示）
    $('#statusText').textContent = '连接中断，重连中…';
  };
}

async function refreshState() {
  try {
    const info = await api('/api/state');
    renderInfo(info);
  } catch (_) {}
}

// ── 启动 ────────────────────────────────────────────────
(async function init() {
  connectSSE();
  await refreshState();
  // 周期刷新状态（IP 会变、设备会上下线）
  setInterval(refreshState, 15000);

  // 调试：?qr=1 直接打开二维码弹层（截图 / 自测用）
  if (/[?&]qr=1\b/.test(location.search)) {
    doQr();
  }
})();

})();
