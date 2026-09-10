const state = { user: null, csrf: "", page: "dashboard", devices: [], presets: [], deviceLimit: 5, requests: [], selectedDeviceId: null, socket: null, reconnectTimer: null, deviceDrafts: {}, requestLoadVersion: 0, pendingSettings: new Set(), pendingRequests: new Set(), answerViews: new Map(), deviceSelectionVersion: 0 };
const activeStatuses = ["waiting_capture", "capturing", "uploading", "processing"];
const $ = (selector) => document.querySelector(selector);
const contentRoot = () => $("#page-content");
function esc(value) { return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) { return {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;"}[c]; }); }
function date(value) { return value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "-"; }
function statusLabel(status) { var labels={online:"在线",offline:"离线",waiting_capture:"等待截屏",capturing:"截屏中",uploading:"上传中",processing:"正在回答",completed:"已完成",failed:"失败",cancelled:"已取消",pending:"待审批",active:"正常",disabled:"已禁用",deleted:"已删除",approved:"已通过",rejected:"已拒绝",expired:"已过期"}; var tone=["completed","online","active","approved"].includes(status)?"success":["failed","cancelled","disabled","rejected"].includes(status)?"danger":["processing","waiting_capture","capturing","uploading","pending"].includes(status)?"warning":"neutral"; return '<span class="status-label '+tone+'"><span class="status-dot '+(tone === "success" ? "online" : tone === "warning" ? "warning" : tone === "danger" ? "danger" : "")+'"></span>'+esc(labels[status] || status)+'</span>'; }
function notify(message, bad) { var node=document.createElement("div"); node.className="toast"+(bad?" error":""); node.textContent=message; var region=$("#toast-region");while(region.children.length>=3)region.firstElementChild.remove();region.appendChild(node); setTimeout(function(){node.remove();},4200); }
function setMessage(selector, message, bad) { var node=$(selector); if(node){node.textContent=message;node.className="form-message"+(bad?" error":" success");} }
function values(form) { return Object.fromEntries(new FormData(form).entries()); }
async function api(path, options) { options=options||{}; var method=(options.method||"GET").toUpperCase(); var headers=new Headers(options.headers||{}); if(options.body && !(options.body instanceof FormData)) headers.set("Content-Type","application/json"); if(method !== "GET" && state.csrf) headers.set("X-CSRF-Token",state.csrf); var response=await fetch(path,Object.assign({},options,{method:method,headers:headers,credentials:"same-origin"})); var data={}; try{data=await response.json();}catch(_){ } if(response.status===401&&state.user)clearSession(); if(!response.ok) throw new Error((data.error&&data.error.message)||("请求失败（"+response.status+"）")); return data; }
let modalReturnFocus=null;
let modalBodyOverflow="";
function modal(inner) { saveDeviceDraft();if(!$(".modal")){modalReturnFocus=document.activeElement;modalBodyOverflow=document.body.style.overflow;}document.body.style.overflow="hidden";$("#modal-root").innerHTML='<div class="modal-backdrop" data-close-modal><div class="modal" role="dialog" aria-modal="true" tabindex="-1" onclick="event.stopPropagation()"><button class="quiet-button modal-close" type="button" aria-label="关闭弹窗" onclick="closeModal()">关闭</button>'+inner+'</div></div>';const dialog=$(".modal");dialog.setAttribute("aria-label",dialog.querySelector("h3")?.textContent||"设置");(dialog.querySelector("input,textarea,select")||dialog.querySelector("button")||dialog).focus(); }
function closeModal() { saveDeviceDraft();$("#modal-root").innerHTML="";document.body.style.overflow=modalBodyOverflow;const trigger=modalReturnFocus?.isConnected?modalReturnFocus:document.getElementById(modalReturnFocus?.id);trigger?.focus();modalReturnFocus=null; }
function showApp() { $("#auth-view").classList.add("hidden");$("#app-view").classList.remove("hidden");$("#account-name").textContent=state.user.display_name;$("#account-role").textContent=state.user.role === "admin" ? "管理员" : "普通用户";document.querySelectorAll(".admin-only").forEach(function(n){n.classList.toggle("hidden",state.user.role !== "admin");});connectBrowser();navigate("dashboard"); }
async function boot() {
  document.addEventListener("keydown",e=>{const dialog=$(".modal");if(!dialog)return;if(e.key==="Escape"){closeModal();return;}if(e.key!=="Tab")return;const fields=Array.from(dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],summary')).filter(x=>x.getClientRects().length);if(!fields.length){e.preventDefault();dialog.focus();return;}const first=fields[0],last=fields[fields.length-1];if(e.shiftKey&&(document.activeElement===first||document.activeElement===dialog)){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}});
  document.addEventListener("input",function(e){if(e.target.closest("#question-form"))saveDeviceDraft();});
  document.addEventListener("change",function(e){if(e.target.closest("#question-form"))saveDeviceDraft();});
  $("#login-form").addEventListener("submit",async function(e){e.preventDefault();try{var r=await api("/api/auth/login",{method:"POST",body:JSON.stringify(values(e.currentTarget))});state.user=r.user;state.csrf=r.csrf_token;showApp();}catch(err){setMessage("#login-message",err.message,true);}});
  $("#register-form").addEventListener("submit",async function(e){e.preventDefault();var form=e.currentTarget;try{await api("/api/auth/register",{method:"POST",body:JSON.stringify(values(form))});form.reset();setMessage("#register-message","注册申请已提交，请等待管理员审批");}catch(err){setMessage("#register-message",err.message,true);}});
  document.querySelectorAll("[data-auth-tab]").forEach(function(button){button.addEventListener("click",function(){document.querySelectorAll("[data-auth-tab]").forEach(function(n){n.classList.toggle("active",n===button);});$("#login-form").classList.toggle("hidden",button.dataset.authTab!=="login");$("#register-form").classList.toggle("hidden",button.dataset.authTab!=="register");});});
  document.querySelectorAll("[data-page]").forEach(function(button){button.addEventListener("click",function(){navigate(button.dataset.page);});});
  $("#logout-button").addEventListener("click",async function(){if(state.socket){state.socket.onclose=null;state.socket.close();state.socket=null;}try{await api("/api/auth/logout",{method:"POST"});clearSession();}catch(err){notify(err.message,true);connectBrowser();}});
  $("#profile-button").addEventListener("click",profileModal);
  $("#modal-root").addEventListener("click",function(e){if(e.target.dataset.closeModal !== undefined)closeModal();});
  var session=await fetch("/api/auth/me",{credentials:"same-origin"}).then(function(r){return r.json();});
  if(session.authenticated){state.user=session.user;state.csrf=session.csrf_token;showApp();}
}
function clearSession(){state.user=null;state.csrf="";clearTimeout(state.reconnectTimer);if(state.socket){state.socket.onclose=null;state.socket.close();state.socket=null;}closeModal();contentRoot().innerHTML="";state.devices=[];state.requests=[];state.presets=[];state.deviceDrafts={};state.selectedDeviceId=null;state.answerViews.clear();state.pendingRequests.clear();state.pendingSettings.clear();$("#auth-view").classList.remove("hidden");$("#app-view").classList.add("hidden");}
function connectBrowser(){
  clearTimeout(state.reconnectTimer);
  if(!state.user)return;
  if(state.socket){state.socket.onclose=null;state.socket.close();}
  var scheme=location.protocol==="https:"?"wss:":"ws:";
  var socket=new WebSocket(scheme+"//"+location.host+"/ws/browser");state.socket=socket;
  socket.onopen=function(){if(state.socket!==socket)return;$("#connection-indicator").innerHTML='<span class="status-dot online"></span><span>已连接</span>';refreshDevices().catch(function(e){notify(e.message,true);});};
  socket.onclose=async function(){if(state.socket!==socket||!state.user)return;$("#connection-indicator").innerHTML='<span class="status-dot warning"></span><span>正在重新连接</span>';try{var current=await api("/api/auth/me");if(!current.authenticated){clearSession();return;}state.user=current.user;state.csrf=current.csrf_token;}catch(_){}if(state.user)state.reconnectTimer=setTimeout(connectBrowser,4000);};
  socket.onerror=function(){if(state.socket===socket)$("#connection-indicator").innerHTML='<span class="status-dot danger"></span><span>连接暂时中断</span>';};
  socket.onmessage=function(e){if(state.socket!==socket)return;try{realtime(JSON.parse(e.data));}catch(err){notify("实时消息处理失败："+err.message,true);}};
}
function saveDeviceDraft() {
  const form=$("#question-form");
  if(form)state.deviceDrafts[form.dataset.deviceId]=values(form);
  syncQuestionIndicator();
}
function restoreDeviceDraft() {
  const form=$("#question-form");
  if(form)form.elements.question.value=state.deviceDrafts[form.dataset.deviceId]?.question||"";
}
function syncQuestionIndicator() {
  const menu=$("#console-menu"),hasQuestion=!!state.deviceDrafts[state.selectedDeviceId]?.question?.trim();
  if(menu){menu.classList.toggle("has-question",hasQuestion);menu.title=hasQuestion?"问答设置（已填写补充问题）":"问答设置";}
}
async function refreshDevices(){
  await loadDevices();
  if(state.page!=="dashboard"||!$("#device-list"))return;
  await loadRequests(state.selectedDeviceId);
  if(state.page!=="dashboard"||!$("#device-list"))return;
  renderDeviceList();renderDeviceDetail();
}
function realtime(message){if(message.type==="sharing_request"){notify("收到电脑共享申请，请打开共享管理处理");if($("#sharing-pending"))loadSharing(Number($("#sharing-pending").dataset.deviceId));}if(message.type === "request_update"&&message.request){var row=message.request;var i=state.requests.findIndex(function(x){return x.id===row.id;});if(i>=0)state.requests[i]=Object.assign({},state.requests[i],row);else state.requests.unshift(row);if(state.page === "dashboard"&&$("#latest-request")&&row.device_id===state.selectedDeviceId){updateLatestAnswer(row);syncRequestControls();}if(state.page === "history"&&["completed","failed","cancelled"].includes(row.status))renderHistory();}if(message.type === "device_update"){refreshDevices().catch(function(e){notify(e.message,true);});}if(message.type === "pairing_update"){notify(message.status === "approved" ? "电脑主人已允许使用" : message.status === "expired" ? "连接许可已失效" : "电脑主人拒绝了申请",message.status !== "approved");refreshDevices().catch(function(e){notify(e.message,true);});}}
async function navigate(page) {
  state.deviceSelectionVersion++;
  saveDeviceDraft();
  rememberAnswerView();
  state.page=page;
  document.querySelectorAll("[data-page]").forEach(b=>{b.classList.toggle("active",b.dataset.page===page);if(b.dataset.page===page)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");});
  contentRoot().classList.toggle("dashboard-page",page==="dashboard");
  try { await renderCurrent(); } catch(err) { notify(err.message,true); }
}
async function renderCurrent() {
  const render={dashboard:renderDashboard,history:renderHistory,presets:renderPresets,users:renderUsers,audit:renderAudit}[state.page];
  if(render)await render();
}
async function loadDevices() {
  const result=await api("/api/devices");state.devices=result.items;state.deviceLimit=result.limit;
  if(!state.devices.some(d=>d.id===state.selectedDeviceId))state.selectedDeviceId=state.devices[0]?.id||null;
}
async function loadRequests(device){var version=++state.requestLoadVersion;var suffix=device ? "?device_id="+encodeURIComponent(device) : "";var result=await api("/api/requests"+suffix);if(version===state.requestLoadVersion)state.requests=result.items;}
function rememberAnswerView() {
  const root=$("#latest-request"),answer=root?.querySelector("[data-answer-id]");
  if(!answer)return;
  state.answerViews.set(answer.dataset.answerId,{scrollTop:root.scrollTop,open:!!answer.querySelector("details[open]"),follow:root.scrollHeight-root.clientHeight-root.scrollTop<40});
  if(state.answerViews.size>100)state.answerViews.delete(state.answerViews.keys().next().value);
}
function restoreAnswerView(follow=false) {
  const root=$("#latest-request"),answer=root?.querySelector("[data-answer-id]");
  const view=state.answerViews.get(answer?.dataset.answerId);
  if(!view)return;
  const reasoning=answer.querySelector("details");if(reasoning)reasoning.open=view.open;
  root.scrollTop=follow&&view.follow?root.scrollHeight:view.scrollTop;
}
function updateLatestAnswer(row) {
  const root=$("#latest-request");
  if(!root||state.requests.find(r=>r.device_id===state.selectedDeviceId)?.id!==row.id)return;
  const focusOnSummary=document.activeElement===root.querySelector("summary");
  rememberAnswerView();root.innerHTML=requestResult(row);restoreAnswerView(true);
  if(focusOnSummary)root.querySelector("summary")?.focus({preventScroll:true});
}
function syncRequestControls() {
  const device=state.devices.find(d=>d.id===state.selectedDeviceId),button=$("#capture-action");
  if(!device||!button)return;
  const active=state.requests.some(r=>r.device_id===device.id&&activeStatuses.includes(r.status));
  const pending=state.pendingRequests.has(device.id),stoppable=active||pending;
  button.className=stoppable?"danger-button":"primary-button";
  button.textContent=stoppable?"停止回答":"截图并提问";
  button.disabled=pending||(!active&&(!device.online||!device.preset_available||state.pendingSettings.has(device.id)));
}
async function cancelActiveRequest() {
  const deviceId=state.selectedDeviceId;
  const active=state.requests.find(r=>r.device_id===deviceId&&activeStatuses.includes(r.status));
  if(!active||state.pendingRequests.has(deviceId))return;
  state.pendingRequests.add(deviceId);syncRequestControls();
  try {
    await api(`/api/requests/${active.id}/cancel`,{method:"POST"});
    if(state.page==="dashboard"&&state.selectedDeviceId===deviceId){await loadRequests(deviceId);if(state.page==="dashboard"&&state.selectedDeviceId===deviceId)renderDeviceDetail();}
  }catch(e){notify(e.message,true);}
  finally{state.pendingRequests.delete(deviceId);syncRequestControls();}
}
function requestResult(row) {
  const placeholder=["failed","cancelled"].includes(row.status)?"没有生成回答":row.status==="processing"?"正在整理回答...":"等待截图和回答...";
  return `<div class="answer-panel" data-answer-id="${esc(row.id)}"><div class="panel-title"><div><h4>回答</h4><p>${date(row.created_at)}${row.preset_name?" · "+esc(row.preset_name):""}</p></div>${statusLabel(row.status)}</div>
    ${row.error?`<div class="request-status failed">${esc(row.error)}</div>`:""}
    ${row.reasoning?`<details class="reasoning-box"><summary>推理内容</summary><div class="prose">${renderMarkdown(row.reasoning)}</div></details>`:""}
    <div class="prose answer-prose">${row.answer?renderMarkdown(row.answer):`<p class="muted">${placeholder}</p>`}</div>
    ${row.screenshot_available?`<div class="toolbar" style="margin-top:14px"><a class="secondary-button" href="${esc(row.screenshot_url)}" target="_blank" rel="noreferrer">查看截图</a></div>`:""}</div>`;
}
async function renderDashboard() {
  contentRoot().innerHTML=`<div class="dashboard-grid"><section class="panel device-sidebar" aria-label="电脑列表"><div class="panel-title"><div><h3>电脑列表</h3><p id="device-count" class="muted"></p></div><button class="quiet-button menu-button" id="computer-menu" aria-label="电脑列表管理" aria-haspopup="dialog"><span class="hamburger" aria-hidden="true"></span></button></div><div id="device-list" tabindex="0">正在加载电脑...</div></section><section id="device-detail" class="panel" aria-label="截图问答操作台"></section></div>`;
  $("#computer-menu").addEventListener("click",()=>controlsModal());
  await loadDevices();await loadRequests(state.selectedDeviceId);
  if(state.page!=="dashboard")return;
  renderDeviceList();renderDeviceDetail();
}
function deviceRows() {
  return state.devices.length?state.devices.map(d=>`<button type="button" class="device-row ${d.id===state.selectedDeviceId?"selected":""}" aria-pressed="${d.id===state.selectedDeviceId}" data-device-id="${d.id}"><div class="device-main"><div class="device-name">${esc(d.name)}</div><div class="device-meta">${esc(d.owner_name)}${state.devices.filter(x=>x.name===d.name).length>1?" · 电脑编号 "+d.id:""}<br>${esc(d.preset?.name||"尚未选择预设")}</div></div><div class="device-state">${statusLabel(d.online?"online":"offline")}</div></button>`).join(""):'<p class="muted">还没有电脑。请打开客户端，填写服务器地址并登录这个账号。</p>';
}
function bindDeviceChoices(root) {
  root.querySelectorAll("[data-device-id]").forEach(button=>button.addEventListener("click",()=>selectDevice(Number(button.dataset.deviceId))));
}
async function selectDevice(id) {
  const version=++state.deviceSelectionVersion;
  saveDeviceDraft();rememberAnswerView();
  try {
    const result=await api("/api/requests?device_id="+encodeURIComponent(id));
    if(version!==state.deviceSelectionVersion||state.page!=="dashboard")return;
    state.requestLoadVersion++;
    state.requests=result.items;
    state.selectedDeviceId=id;
    if($("#computer-choices"))closeModal();
    renderDeviceList();renderDeviceDetail();
  }catch(e){notify(e.message,true);}
}
function renderDeviceList() {
  const list=$("#device-list");if(!list)return;
  $("#device-count").textContent=`我的电脑 ${state.devices.filter(d=>d.owner_id===state.user.id).length} / ${state.deviceLimit} 台`;
  list.innerHTML=deviceRows();bindDeviceChoices(list);
  const choices=$("#computer-choices");if(choices){choices.innerHTML=deviceRows();bindDeviceChoices(choices);}
}
function computerPicker() {
  modal(`<h3>选择电脑</h3><div id="computer-choices" class="computer-choices">${deviceRows()}</div><div class="modal-actions"><button type="button" class="secondary-button" id="picker-manage">电脑列表管理</button></div>`);
  bindDeviceChoices($("#computer-choices"));
  $("#picker-manage").addEventListener("click",()=>controlsModal());
}
function renderDeviceDetail() {
  const root=$("#device-detail"),device=state.devices.find(d=>d.id===state.selectedDeviceId);if(!root)return;
  saveDeviceDraft();rememberAnswerView();
  if(!device){root.innerHTML='<div class="empty-console"><button class="secondary-button" id="empty-computer-picker">选择电脑</button></div><div class="detail-empty"><i class="ri ri-computer" aria-hidden="true"></i><strong>连接你的第一台电脑</strong><span>打开客户端并登录同一账号。</span></div>';$("#empty-computer-picker").addEventListener("click",computerPicker);return;}
  const own=device.owner_id===state.user.id||state.user.role==="admin";
  const latest=state.requests.find(r=>r.device_id===device.id);
  root.innerHTML=`<div class="console-controls"><form id="request-form" data-device-id="${device.id}" class="console-bar">
    <div class="console-device"><div class="desktop-device-name" title="${esc(device.name)}">${statusLabel(device.online?"online":"offline")}<strong>${esc(device.name)}</strong></div><button type="button" class="secondary-button mobile-device-picker" id="select-computer" aria-haspopup="dialog" aria-label="选择电脑"><span class="status-dot ${device.online?"online":""}" aria-hidden="true"></span><span>${esc(device.name)}</span><span aria-hidden="true">⌄</span></button></div>
    <div class="console-preset">${own?`<button type="button" class="secondary-button preset-picker-button" id="device-preset" data-device-id="${device.id}" aria-haspopup="dialog" aria-label="选择预设：${esc(device.preset?.name||"尚未选择")}" title="${esc(device.preset?.name||"选择预设")}" ${state.pendingSettings.has(device.id)?"disabled":""}><span>${esc(device.preset?.name||"选择预设")}</span><span aria-hidden="true">⌄</span></button>`:`<span class="shared-preset" title="由电脑主人设置">预设：${esc(device.preset?.name||"尚未选择")}</span>`}</div>
    <button class="primary-button" id="capture-action" type="submit" disabled>截图并提问</button><button type="button" class="quiet-button menu-button" id="console-menu" aria-label="问答设置" aria-haspopup="dialog"><span class="hamburger" aria-hidden="true"></span></button></form>
    ${!device.preset_available?`<p class="request-status failed">${own?"请选择可用预设。":"请联系电脑主人选择可用预设。"}</p>`:""}
    ${!device.online?'<p class="request-status">这台电脑尚未连接，请打开客户端并登录。</p>':""}</div>
    <div id="latest-request" tabindex="0" aria-label="当前回答">${latest?requestResult(latest):'<div class="detail-empty"><i class="ri ri-screenshot" aria-hidden="true"></i><strong>准备好后，点击截图并提问</strong><span>回答和截图会保存在问答历史中。</span></div>'}</div>`;
  $("#request-form").addEventListener("submit",e=>{
    e.preventDefault();
    if(state.requests.some(r=>r.device_id===device.id&&activeStatuses.includes(r.status)))cancelActiveRequest();
    else submitRequest(e);
  });
  $("#console-menu").addEventListener("click",()=>controlsModal(device));
  $("#select-computer").addEventListener("click",computerPicker);
  if(own)$("#device-preset").addEventListener("click",()=>presetPicker(device));
  restoreAnswerView();syncRequestControls();syncQuestionIndicator();
}
async function presetPicker(device) {
  if(state.pendingSettings.has(device.id))return;
  modal(`<h3>选择预设</h3><div id="preset-choices" class="preset-choices" aria-busy="true"><p class="muted" role="status">正在加载预设...</p></div><p id="preset-picker-message" class="form-message" role="status"></p><div class="modal-actions"><button type="button" class="secondary-button" id="picker-manage-presets">管理预设</button></div>`);
  const choices=$("#preset-choices"),message=$("#preset-picker-message");
  $("#picker-manage-presets").addEventListener("click",()=>{closeModal();navigate("presets");});
  try {
    const result=await api(`/api/devices/${device.id}/settings`);
    if(!choices.isConnected)return;
    const selectedId=state.devices.find(d=>d.id===device.id)?.preset?.id;
    choices.innerHTML=result.presets.length?result.presets.map(p=>`<button type="button" class="preset-choice ${p.id===selectedId?"selected":""}" data-preset-id="${p.id}" aria-pressed="${p.id===selectedId}"><span class="preset-choice-info"><strong>${esc(p.name)}</strong><span class="muted">${p.is_global?"公共预设":"个人预设"}${result.presets.filter(x=>x.name===p.name).length>1?" · 编号 "+p.id:""}</span></span><span class="preset-choice-check">${p.id===selectedId?"已选中":"选择"}</span></button>`).join(""):'<p class="muted">暂无可用预设，请先在管理预设中创建。</p>';
    choices.querySelectorAll("[data-preset-id]").forEach(button=>button.addEventListener("click",async()=>{
      if(state.pendingSettings.has(device.id))return;
      const presetId=Number(button.dataset.presetId);
      if(presetId===state.devices.find(d=>d.id===device.id)?.preset?.id){closeModal();return;}
      state.pendingSettings.add(device.id);syncRequestControls();
      choices.querySelectorAll("button").forEach(b=>b.disabled=true);
      const trigger=$("#device-preset");if(trigger?.dataset.deviceId===String(device.id))trigger.disabled=true;
      message.className="form-message";message.textContent="正在保存...";
      let saved=false;
      try {
        await api(`/api/devices/${device.id}/settings`,{method:"PUT",body:JSON.stringify({preset_id:presetId})});
        saved=true;notify("预设已更新");await refreshDevices();
      }catch(e){if(saved||!message.isConnected)notify(e.message,true);else{message.className="form-message error";message.textContent=e.message;}}
      finally {
        state.pendingSettings.delete(device.id);
        choices.querySelectorAll("button").forEach(b=>b.disabled=false);
        const current=$("#device-preset");if(current?.dataset.deviceId===String(device.id))current.disabled=false;
        syncRequestControls();
        if(saved&&choices.isConnected)closeModal();
      }
    }));
  }catch(e){
    if(!choices.isConnected)return;
    choices.innerHTML='<button type="button" class="secondary-button" id="retry-presets">重新加载</button>';
    message.className="form-message error";message.textContent=e.message;
    choices.querySelector("button").addEventListener("click",()=>presetPicker(device));
  }finally{choices.setAttribute("aria-busy","false");}
}
function controlsModal(device) {
  const own=device&&(device.owner_id===state.user.id||state.user.role==="admin");
  modal(`<h3>${device?"问答设置 · "+esc(device.name):"电脑列表管理"}</h3><div class="settings-stack">
    ${device?`<form id="question-form" data-device-id="${device.id}" class="stack-form"><label>补充问题（可不填）<textarea name="question" rows="4" maxlength="5000" placeholder="例如：请解释屏幕上这道题的解法"></textarea></label><button type="submit" class="primary-button">完成</button></form>`:""}
    ${device?own?`<div class="settings-actions"><button class="secondary-button" id="manage-presets">管理预设</button><button class="secondary-button" id="manage-device">电脑设置</button><button class="secondary-button" id="share-device">共享管理</button></div>`:`<p>当前预设：${esc(device.preset?.name||"尚未选择")}</p><button class="secondary-button" id="remove-device">从列表移除</button>`:""}
    <div class="settings-section"><button class="secondary-button" id="refresh-devices">刷新电脑列表</button></div>
    <form id="pair-form" class="stack-form settings-section"><label>添加其他账号的电脑<input class="code-input" name="connection_code" inputmode="numeric" maxlength="9" pattern="[0-9]{9}" placeholder="邀请码，例如：123456789" required></label><button class="secondary-button" type="submit">申请使用</button></form></div>`);
  $("#pair-form").addEventListener("submit",pairDevice);
  $("#refresh-devices").addEventListener("click",async e=>{const b=e.currentTarget;b.disabled=true;try{await refreshDevices();notify("电脑列表已刷新");}catch(err){notify(err.message,true);}finally{b.disabled=false;}});
  if(device){restoreDeviceDraft();$("#question-form").addEventListener("submit",e=>{e.preventDefault();closeModal();});}
  if(own){
    $("#manage-presets").addEventListener("click",()=>{closeModal();navigate("presets");});
    $("#manage-device").addEventListener("click",()=>computerModal(device));
    $("#share-device").addEventListener("click",()=>sharingModal(device));
  }else if(device)$("#remove-device").addEventListener("click",()=>deviceAction("unpair",device.id));
}
function computerModal(device) {
  modal(`<h3>电脑设置</h3><form id="computer-settings-form" class="stack-form"><label>电脑名称<input name="name" value="${esc(device.name)}" maxlength="120" placeholder="例如：书房电脑" required></label><p class="muted">电脑编号：${device.id} · 客户端版本：${esc(device.client_version||"尚未连接")}</p><p id="computer-message" class="form-message"></p><div class="modal-actions"><button type="button" class="secondary-button" onclick="closeModal()">取消</button><button class="primary-button" type="submit">保存名称</button></div></form><hr><div class="toolbar"><button id="disconnect-device" class="secondary-button">断开连接</button><button id="delete-device" class="danger-button">删除电脑</button></div>`);
  $("#computer-settings-form").addEventListener("submit",async e=>{e.preventDefault();const button=e.currentTarget.querySelector('[type="submit"]');button.disabled=true;try{await api(`/api/devices/${device.id}/settings`,{method:"PUT",body:JSON.stringify(values(e.currentTarget))});closeModal();notify("电脑名称已保存");await refreshDevices();}catch(err){setMessage("#computer-message",err.message,true);button.disabled=false;}});
  $("#disconnect-device").addEventListener("click",()=>deviceAction("disconnect",device.id));
  $("#delete-device").addEventListener("click",()=>deviceAction("delete",device.id));
}
function sharingModal(device) {
  modal(`<h3>共享 · ${esc(device.name)}</h3><p class="muted">将邀请码交给其他账号，并在下方确认对方的使用申请。</p><div class="toolbar"><button class="primary-button" id="create-invitation">生成新邀请码</button><button class="danger-button" id="revoke-sharing">取消全部共享</button></div><p class="muted">生成新邀请码会撤销之前的共享权限。</p><div id="invitation-code"></div><div class="panel-title"><h4>待处理申请</h4><button class="quiet-button" id="refresh-sharing">刷新</button></div><div id="sharing-pending" data-device-id="${device.id}">正在加载...</div>`);
  $("#create-invitation").addEventListener("click",async e=>{if(!confirm("生成新邀请码并撤销原有共享权限？"))return;const button=e.currentTarget;button.disabled=true;try{const r=await api(`/api/devices/${device.id}/invitation`,{method:"POST"});if($("#invitation-code"))$("#invitation-code").innerHTML=`<p class="invitation-code">${esc(r.connection_code)}</p>`;loadSharing(device.id);}catch(err){notify(err.message,true);}finally{button.disabled=false;}});
  $("#revoke-sharing").addEventListener("click",()=>deviceAction("unpair",device.id));
  $("#refresh-sharing").addEventListener("click",()=>loadSharing(device.id));loadSharing(device.id);
}
async function loadSharing(deviceId){
  try {const r=await api(`/api/devices/${deviceId}/sharing`),root=$("#sharing-pending");if(!root||Number(root.dataset.deviceId)!==deviceId)return;
    root.innerHTML=r.items.length?r.items.map(p=>`<div class="sharing-row"><div><strong>${esc(p.display_name)}</strong><p class="muted">${esc(p.username)}</p></div><div class="toolbar"><button class="primary-button" data-pair-id="${esc(p.id)}" data-decision="approve">允许</button><button class="secondary-button" data-pair-id="${esc(p.id)}" data-decision="reject">拒绝</button></div></div>`).join(""):'<p class="muted">暂无待处理申请</p>';
    root.querySelectorAll('[data-pair-id]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;try{await api(`/api/devices/${deviceId}/sharing/${b.dataset.pairId}`,{method:"POST",body:JSON.stringify({decision:b.dataset.decision})});await loadSharing(deviceId);}catch(e){notify(e.message,true);b.disabled=false;}}));
  }catch(e){notify(e.message,true);}
}
async function pairDevice(e){e.preventDefault();try{var r=await api("/api/devices/pair",{method:"POST",body:JSON.stringify({connection_code:new FormData(e.currentTarget).get("connection_code")})});if(r.status==="approved"){notify("设备已授权");await loadDevices();renderDashboard();return;}notify("申请已发送，请等待电脑主人在网页确认");var id=r.pair_request_id,timer=setInterval(async function(){try{var x=await api("/api/pairings/"+id);if(x.status!=="pending"){clearInterval(timer);notify(x.status==="approved"?"电脑主人已授权":({rejected:"对方未同意",expired:"申请已过期"})[x.status]||"申请已结束",x.status!=="approved");await refreshDevices();}}catch(_){clearInterval(timer);}},1000);}catch(err){notify(err.message,true);}}
async function submitRequest(e) {
  e.preventDefault();const deviceId=Number(e.currentTarget.dataset.deviceId);saveDeviceDraft();const question=state.deviceDrafts[deviceId]?.question||"";
  if(state.pendingSettings.has(deviceId)||state.pendingRequests.has(deviceId))return;
  state.pendingRequests.add(deviceId);syncRequestControls();
  try {
    const r=await api("/api/devices/"+deviceId+"/requests",{method:"POST",body:JSON.stringify({question})});
    const live=state.requests.find(row=>row.id===r.request.id);
    state.requests=state.requests.filter(row=>row.id!==r.request.id);state.requests.unshift(live||r.request);
    if((state.deviceDrafts[deviceId]?.question||"")===question){delete state.deviceDrafts[deviceId];const draftForm=$("#question-form");if(draftForm?.dataset.deviceId===String(deviceId))draftForm.reset();}syncQuestionIndicator();notify("正在截图，请稍候");
    if(state.page==="dashboard"&&state.selectedDeviceId===deviceId)renderDeviceDetail();
  }catch(err){notify(err.message,true);}
  finally{state.pendingRequests.delete(deviceId);syncRequestControls();}
}
async function deviceAction(action,deviceId=state.selectedDeviceId) {
  const d=state.devices.find(x=>x.id===deviceId);if(!d)return;
  const message=action==="delete"?`删除“${d.name}”？这台电脑会退出登录，并释放一个电脑名额。已有回答会保留。`:action==="unpair"?(d.owner_id===state.user.id||state.user.role==="admin"?"取消所有共享权限和邀请码？":"从列表中移除这台电脑？"):"断开这台电脑？需要在客户端重新连接后才能继续提问。";
  if(!confirm(message))return;
  try {
    await api("/api/devices/"+d.id+(action==="delete"?"":"/"+action),{method:action==="delete"?"DELETE":"POST"});
    closeModal();
    notify(action==="delete"?"电脑已删除，可以登录新电脑":action==="unpair"?"已移除":"已断开连接");
    await refreshDevices();
  }catch(err){notify(err.message,true);}
}
async function renderHistory(){contentRoot().innerHTML='<div class="page-toolbar"><button id="refresh-history" class="secondary-button">刷新记录</button></div><section class="panel"><div id="history-list"><p class="muted">正在加载...</p></div></section>';$("#refresh-history").addEventListener("click",renderHistory);try{await loadRequests();var list=$("#history-list");if(!list)return;list.innerHTML=state.requests.length?state.requests.map(function(r){return '<article class="history-item" data-request-id="'+r.id+'"><div class="history-head"><strong>'+esc(r.device_name)+'</strong>'+statusLabel(r.status)+'</div><div class="history-question">'+esc(r.question||"未填写补充问题")+'</div><div class="muted">'+date(r.created_at)+' · '+esc(r.preset_name||"屏幕问答")+'</div></article>';}).join(""): '<p class="muted">暂无记录</p>';list.querySelectorAll("[data-request-id]").forEach(function(n){n.addEventListener("click",function(){var r=state.requests.find(function(x){return x.id===n.dataset.requestId;});if(r){modal('<h3>问答详情</h3>'+requestResult(r)+'<div class="modal-actions"><button class="secondary-button" onclick="closeModal()">关闭</button></div>');}});});}catch(err){notify(err.message,true);}}

async function renderPresets() {
  contentRoot().innerHTML=`<div class="page-toolbar"><button class="primary-button" id="new-preset">新建预设</button></div><section class="preset-catalog" id="preset-list">正在加载预设...</section>`;
  $("#new-preset").addEventListener("click",()=>presetModal());
  const result=await api("/api/presets");if(state.page!=="presets")return;state.presets=result.items;
  const list=$("#preset-list");
  list.innerHTML=state.presets.length?state.presets.map(p=>`<article class="preset-card"><div><h3>${esc(p.name)} <span class="role-label">${p.is_global?"公共预设":"我的预设"}</span></h3><p class="muted">${esc(p.description||"已保存提示词和模型配置")}</p>${!p.enabled?'<p class="form-message error">已停用，无法用于新提问</p>':""}</div>${p.editable?`<div class="toolbar"><button class="secondary-button" data-edit-preset="${p.id}">编辑</button><button class="secondary-button" data-toggle-preset="${p.id}">${p.enabled?"停用":"启用"}</button></div>`:'<span class="muted">可在截图问答页选择使用</span>'}</article>`).join(""):'<p class="muted">还没有预设。点击新建预设，或请管理员提供公共预设。</p>';
  list.querySelectorAll("[data-edit-preset]").forEach(b=>b.addEventListener("click",()=>presetModal(state.presets.find(p=>p.id===Number(b.dataset.editPreset)))));
  list.querySelectorAll("[data-toggle-preset]").forEach(b=>b.addEventListener("click",async()=>{
    const p=state.presets.find(p=>p.id===Number(b.dataset.togglePreset));
    if(!confirm(p.enabled?"停用后，使用这个预设的电脑需要重新选择预设。继续吗？":"重新启用这个预设？"))return;
    try {await api("/api/presets/"+p.id,{method:"PATCH",body:JSON.stringify({enabled:!p.enabled})});await renderPresets();}catch(e){notify(e.message,true);}
  }));
}
function presetModal(p) {
  const option=(value,label,current)=>`<option value="${value}" ${value===current?"selected":""}>${label}</option>`;
  modal(`<h3>${p?"编辑预设":"新建预设"}</h3><form id="preset-form" class="stack-form"><label>预设名称<input name="name" maxlength="120" value="${esc(p?.name||"")}" placeholder="例如：讲解屏幕上的题目" required></label>
    <label>用途说明<input name="description" maxlength="500" value="${esc(p?.description||"")}" placeholder="用一句话说明适合什么场景"></label>
    <label>提示词<textarea name="prompt" rows="4" maxlength="20000" placeholder="例如：先给出答案，再用简单的语言讲解步骤。" required>${esc(p?.prompt||"")}</textarea></label>
    <fieldset><legend>模型配置</legend><div class="form-grid">
    <label class="full">请求格式<select name="provider">${[["openai_chat","OpenAI Chat Completions"],["openai_responses","OpenAI Responses"],["deepseek","DeepSeek Chat Completions"],["gemini","Gemini generateContent / streamGenerateContent"]].map(([v,l])=>option(v,l,p?.provider||"openai_chat")).join("")}</select></label>
    <label>模型名称<input name="model_name" maxlength="160" value="${esc(p?.model_name||"")}" placeholder="例如：gpt-4.1（需支持图像输入）" required></label>
    <label class="full">模型端点<input name="endpoint" aria-label="模型端点" aria-describedby="endpoint-note" type="url" maxlength="1000" value="${esc(p?.endpoint||"")}" placeholder="例如：https://api.openai.com/v1" required><small class="muted endpoint-note" id="endpoint-note"></small></label>
    <label class="full">API 密钥${p?"（留空沿用已保存的密钥）":""}<input name="api_key" type="password" autocomplete="new-password" maxlength="4000" placeholder="例如：sk-…，填写服务商提供的 API 密钥" ${p?"":"required"}></label></div></fieldset>
    <details><summary>高级参数</summary><div class="form-grid"><label>推理强度（reasoning_effort）<select name="reasoning_effort">${[["none","none（不指定推理强度）"],["low","low（低）"],["medium","medium（中）"],["high","high（高）"],["xhigh","xhigh（极高）"]].map(([v,l])=>option(v,l,p?.reasoning_effort||"none")).join("")}</select><small class="muted">none：Chat Completions / Responses 不发送推理强度参数；Gemini 设置 thinkingBudget=0。</small></label>
    <label>请求超时（秒）<input name="timeout_seconds" type="number" min="10" max="600" value="${p?.timeout_seconds||120}"></label>
    <label>采样温度（temperature，可留空）<input name="temperature" placeholder="例如：0.7" type="number" min="0" max="2" step="0.1" value="${esc(p?.options?.temperature??"")}"></label>
    <label>输出 Token 上限（可留空）<input name="output_limit" placeholder="例如：4096" type="number" min="1" value="${esc(p?.options?.max_output_tokens??p?.options?.max_completion_tokens??p?.options?.max_tokens??"")}"></label></div></details>
    ${state.user.role==="admin"&&!p?'<label class="checkbox-line"><input name="is_global" type="checkbox"> 公共预设</label>':""}
    <p id="preset-message" class="form-message"></p><div class="modal-actions"><button class="secondary-button" type="button" onclick="closeModal()">取消</button><button class="primary-button" type="submit">保存预设</button></div></form>`);
  const presetForm=$("#preset-form"),endpointInput=presetForm.elements.endpoint;
  const updateEndpointHint=()=>{
    const provider=presetForm.elements.provider.value,gemini=provider==="gemini";
    const example=gemini?"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse":provider==="deepseek"?"https://api.deepseek.com/v1":"https://api.openai.com/v1";
    endpointInput.placeholder="例如："+example;
    presetForm.elements.model_name.placeholder=gemini?"例如：gemini-2.5-flash":provider==="deepseek"?"填写服务商支持图像输入的模型 ID":"例如：gpt-4.1（需支持图像输入）";
    const base=endpointInput.value.trim().replace(/\/+$/, "").replace(/\/(chat\/completions|responses?)$/, "");
    const normalized=base?(base.endsWith("/v1")?base:base+"/v1"):example;
    $("#endpoint-note").textContent=gemini?"示例："+example+"。{model} 会替换为模型名称。":"填写截至 /v1 的基础地址。实际请求："+normalized+(provider==="openai_responses"?"/responses":"/chat/completions");
  };
  presetForm.elements.provider.addEventListener("change",updateEndpointHint);endpointInput.addEventListener("input",updateEndpointHint);updateEndpointHint();
  $("#preset-form").addEventListener("submit",async e=>{
    e.preventDefault();const form=e.currentTarget,v=values(form),button=form.querySelector('[type="submit"]');button.disabled=true;
    const options=Object.assign({},p?.options||{});delete options.temperature;delete options.max_tokens;delete options.max_completion_tokens;delete options.max_output_tokens;
    if(v.temperature!=="")options.temperature=Number(v.temperature);
    if(v.output_limit!=="")options[["gemini","openai_responses"].includes(v.provider)?"max_output_tokens":"max_tokens"]=Number(v.output_limit);
    const payload={name:v.name,description:v.description,prompt:v.prompt,provider:v.provider,endpoint:v.endpoint,api_key:v.api_key,model_name:v.model_name,reasoning_effort:v.reasoning_effort,timeout_seconds:Number(v.timeout_seconds),options,is_global:p?p.is_global:!!form.elements.is_global?.checked};
    try {await api(p?"/api/presets/"+p.id:"/api/presets",{method:p?"PUT":"POST",body:JSON.stringify(payload)});closeModal();notify("预设已保存，可在截图问答页选择");await renderPresets();}catch(err){setMessage("#preset-message",err.message,true);button.disabled=false;}
  });
}

async function renderUsers(){if(state.user.role!=="admin")return navigate("dashboard");contentRoot().innerHTML='<div class="page-toolbar"><button class="secondary-button" id="refresh-users">刷新</button></div><section class="panel"><div id="user-table" class="data-table-wrap"><p class="muted">正在加载...</p></div></section>';$("#refresh-users").addEventListener("click",renderUsers);var rows=(await api("/api/admin/users")).items;$("#user-table").innerHTML='<table><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>注册备注</th><th>创建时间</th><th>操作</th></tr></thead><tbody>'+rows.map(function(r){return '<tr><td><strong>'+esc(r.display_name)+'</strong><div class="muted">'+esc(r.username)+'</div></td><td>'+(r.role==="admin"?"管理员":"普通用户")+'</td><td>'+statusLabel(r.status)+(r.locked_until?'<div class="muted">锁定至 '+date(r.locked_until)+'</div>':'')+'</td><td>'+esc(r.registration_note)+'</td><td>'+date(r.created_at)+'</td><td><div class="toolbar">'+(r.status==="pending"?'<button class="primary-button" data-user-action="approve" data-user-id="'+r.id+'">批准</button>':'')+(r.status==="active"&&r.id!==state.user.id?'<button class="danger-button" data-user-action="disable" data-user-id="'+r.id+'">禁用</button>':'')+(r.status==="disabled"?'<button class="secondary-button" data-user-action="enable" data-user-id="'+r.id+'">启用</button>':'')+'<button class="secondary-button" data-user-action="unlock" data-user-id="'+r.id+'">解锁</button><button class="secondary-button" data-user-action="reset" data-user-id="'+r.id+'">重置密码</button>'+(r.id!==state.user.id&&r.status!=="deleted"?'<button class="danger-button" data-user-action="delete" data-user-id="'+r.id+'">删除</button>':'')+'</div></td></tr>';}).join("")+'</tbody></table>';$("#user-table").querySelectorAll("[data-user-action]").forEach(function(b){b.addEventListener("click",function(){userAction(Number(b.dataset.userId),b.dataset.userAction);});});}
async function userAction(id,action){if(action==="delete"){if(!confirm("确定删除账号？"))return;try{await api("/api/admin/users/"+id,{method:"DELETE"});notify("账号已删除");renderUsers();}catch(err){notify(err.message,true);}return;}var body={};if(action==="approve"||action==="enable")body.status="active";if(action==="disable")body.status="disabled";if(action==="unlock")body.unlock=true;if(action==="reset"){var p=prompt("请输入新的临时密码");if(!p)return;body.reset_password=p;}try{await api("/api/admin/users/"+id,{method:"PATCH",body:JSON.stringify(body)});notify("账号已更新");renderUsers();}catch(err){notify(err.message,true);}}
async function renderAudit() {
  if(state.user.role!=="admin")return navigate("dashboard");
  const actions={"user.register":"申请注册", "auth.login":"登录账号", "auth.login_failed":"登录未成功", "auth.logout":"退出登录", "auth.password_changed":"修改密码", "device.pair_requested":"申请使用电脑", "device.unpaired":"取消电脑共享", "device.disconnected":"断开电脑", "request.created":"截图并提问", "request.cancelled":"停止回答", "user.updated":"更新账号设置", "user.deleted":"删除账号", "device.settings_updated":"修改电脑设置", "device.invitation_created":"生成邀请码", "device.sharing_decided":"处理共享申请"};
  const targets={user:"用户",device:"设备",request:"请求"};
  contentRoot().innerHTML='<div class="page-toolbar"><button class="secondary-button" id="refresh-audit">刷新</button></div><section class="panel"><div id="audit-table" class="data-table-wrap">正在加载...</div></section>';
  $("#refresh-audit").addEventListener("click",renderAudit);
  const rows=(await api("/api/admin/audit-logs")).items;if(state.page!=="audit")return;
  $("#audit-table").innerHTML='<table><thead><tr><th>时间</th><th>账号</th><th>操作</th><th>对象</th><th>IP 地址</th></tr></thead><tbody>'+rows.map(r=>`<tr><td>${date(r.created_at)}</td><td>${esc(r.username==="system"?"系统":r.username)}</td><td>${esc(actions[r.action]||r.action)}<div class="muted">${esc(r.action)}</div></td><td>${esc(targets[r.target_type]||r.target_type)} ${esc(r.target_id||"")}</td><td>${esc(r.ip_address||"")}</td></tr>`).join("")+'</tbody></table>';
}
function profileModal(){modal('<h3>账号设置</h3><p class="muted">当前账号：'+esc(state.user.username)+' · '+esc(state.user.display_name)+'</p><form id="password-form" class="stack-form"><label>当前密码<input name="current_password" type="password" required></label><label>新密码<input name="new_password" type="password" minlength="8" required></label><div class="modal-actions"><button type="button" class="secondary-button" onclick="closeModal()">取消</button><button class="primary-button" type="submit">修改密码</button></div></form>');$("#password-form").addEventListener("submit",async function(e){e.preventDefault();try{var result=await api("/api/auth/change-password",{method:"POST",body:JSON.stringify(values(e.currentTarget))});state.csrf=result.csrf_token;closeModal();notify("密码已修改");connectBrowser();}catch(err){notify(err.message,true);}});}
boot().catch(function(err){notify(err.message,true);});

