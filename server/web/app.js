const state = { user: null, csrf: "", page: "dashboard", devices: [], presets: [], deviceLimit: 5, requests: [], selectedDeviceId: null, socket: null, reconnectTimer: null, deviceDrafts: {}, requestLoadVersion: 0 };
const $ = (selector) => document.querySelector(selector);
const contentRoot = () => $("#page-content");
function esc(value) { return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) { return {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;"}[c]; }); }
function date(value) { return value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "-"; }
function statusLabel(status) { var labels={online:"在线",offline:"离线",waiting_capture:"等待截屏",capturing:"截屏中",uploading:"上传中",processing:"正在回答",completed:"已完成",failed:"失败",cancelled:"已取消",pending:"待审批",active:"正常",disabled:"已禁用",deleted:"已删除",approved:"已通过",rejected:"已拒绝",expired:"已过期"}; var tone=["completed","online","active","approved"].includes(status)?"success":["failed","cancelled","disabled","rejected"].includes(status)?"danger":["processing","waiting_capture","capturing","uploading","pending"].includes(status)?"warning":"neutral"; return '<span class="status-label '+tone+'"><span class="status-dot '+(tone === "success" ? "online" : tone === "warning" ? "warning" : tone === "danger" ? "danger" : "")+'"></span>'+esc(labels[status] || status)+'</span>'; }
function notify(message, bad) { var node=document.createElement("div"); node.className="toast"+(bad?" error":""); node.textContent=message; var region=$("#toast-region");while(region.children.length>=3)region.firstElementChild.remove();region.appendChild(node); setTimeout(function(){node.remove();},4200); }
function setMessage(selector, message, bad) { var node=$(selector); if(node){node.textContent=message;node.className="form-message"+(bad?" error":" success");} }
function values(form) { return Object.fromEntries(new FormData(form).entries()); }
async function api(path, options) { options=options||{}; var method=(options.method||"GET").toUpperCase(); var headers=new Headers(options.headers||{}); if(options.body && !(options.body instanceof FormData)) headers.set("Content-Type","application/json"); if(method !== "GET" && state.csrf) headers.set("X-CSRF-Token",state.csrf); var response=await fetch(path,Object.assign({},options,{method:method,headers:headers,credentials:"same-origin"})); var data={}; try{data=await response.json();}catch(_){ } if(response.status===401&&state.user)clearSession(); if(!response.ok) throw new Error((data.error&&data.error.message)||("请求失败（"+response.status+"）")); return data; }
function modal(inner) { $("#modal-root").innerHTML='<div class="modal-backdrop" data-close-modal><div class="modal" onclick="event.stopPropagation()">'+inner+'</div></div>'; }
function closeModal() { $("#modal-root").innerHTML=""; }
function showApp() { $("#auth-view").classList.add("hidden");$("#app-view").classList.remove("hidden");$("#account-name").textContent=state.user.display_name;$("#account-role").textContent=state.user.role === "admin" ? "管理员" : "普通用户";document.querySelectorAll(".admin-only").forEach(function(n){n.classList.toggle("hidden",state.user.role !== "admin");});connectBrowser();navigate("dashboard"); }
async function boot() {
  document.addEventListener("input",function(e){if(e.target.closest("#request-form"))saveDeviceDraft();});
  document.addEventListener("change",function(e){if(e.target.closest("#request-form"))saveDeviceDraft();});
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
function clearSession(){state.user=null;state.csrf="";clearTimeout(state.reconnectTimer);if(state.socket){state.socket.onclose=null;state.socket.close();state.socket=null;}closeModal();contentRoot().innerHTML="";state.devices=[];state.requests=[];state.presets=[];state.deviceDrafts={};state.selectedDeviceId=null;$("#auth-view").classList.remove("hidden");$("#app-view").classList.add("hidden");}
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
function saveDeviceDraft(){var form=$("#request-form");if(form&&state.selectedDeviceId)state.deviceDrafts[form.dataset.deviceId||state.selectedDeviceId]=values(form);}
function restoreDeviceDraft(){
  var form=$("#request-form"),draft=state.deviceDrafts[state.selectedDeviceId];
  if(!form||!draft)return;
  ["question"].forEach(function(name){var input=form.elements.namedItem(name);if(input&&draft[name]!==undefined){input.value=draft[name];if(name==="model_profile_id")input.dispatchEvent(new Event("change"));}});
  form.elements.namedItem("force").checked=draft.force==="on";
}
async function refreshDevices(){
  await loadDevices();
  if(state.page!=="dashboard"||!$("#device-list"))return;
  await loadRequests(state.selectedDeviceId);
  if(state.page!=="dashboard"||!$("#device-list"))return;
  renderDeviceList();renderDeviceDetail();
}
function realtime(message){if(message.type === "request_update"&&message.request){var row=message.request;var i=state.requests.findIndex(function(x){return x.id===row.id;});if(i>=0)state.requests[i]=Object.assign({},state.requests[i],row);else state.requests.unshift(row);if(state.page === "dashboard"&&$("#latest-request")&&row.device_id===state.selectedDeviceId){$("#latest-request").innerHTML=requestResult(row);if(["completed","failed","cancelled"].includes(row.status)&&$("#cancel-active"))$("#cancel-active").remove();}if(state.page === "history"&&["completed","failed","cancelled"].includes(row.status))renderHistory();}if(message.type === "device_update"){refreshDevices().catch(function(e){notify(e.message,true);});}if(message.type === "pairing_update"){notify(message.status === "approved" ? "客户端已许可连接" : message.status === "expired" ? "连接许可已失效" : "客户端拒绝了连接",message.status !== "approved");refreshDevices().catch(function(e){notify(e.message,true);});}}
async function navigate(page) {
  saveDeviceDraft();
  state.page=page;
  document.querySelectorAll("[data-page]").forEach(b=>b.classList.toggle("active",b.dataset.page===page));
  const title={dashboard:"我的电脑",history:"问答历史",presets:"预设",users:"账号管理",audit:"操作记录"}[page]||"我的电脑";
  $("#page-eyebrow").textContent="屏幕问答";
  $("#page-title").textContent=title;
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
function requestResult(row) {
  const placeholder=["failed","cancelled"].includes(row.status)?"没有生成回答":row.status==="processing"?"正在整理回答...":"等待截图和回答...";
  return `<div class="answer-panel"><div class="panel-title"><div><h4>回答</h4><p>${date(row.created_at)}${row.preset_name?" · "+esc(row.preset_name):""}</p></div>${statusLabel(row.status)}</div>
    ${row.error?`<div class="request-status failed">${esc(row.error)}</div>`:""}
    ${row.reasoning?`<details class="reasoning-box"><summary>查看思考过程</summary><div class="prose">${esc(row.reasoning)}</div></details>`:""}
    <div class="prose answer-prose">${esc(row.answer||placeholder)}</div>
    ${row.screenshot_available?`<div class="toolbar" style="margin-top:14px"><a class="secondary-button" href="${esc(row.screenshot_url)}" target="_blank" rel="noreferrer">查看截图</a></div>`:""}</div>`;
}
async function renderDashboard() {
  contentRoot().innerHTML=`<div class="page-intro"><div><h3>选择电脑，开始提问</h3><p class="muted">在客户端登录相同账号，这台电脑就会出现在这里。</p></div>
    <details><summary>添加其他账号的电脑</summary><form id="pair-form" class="inline-form"><label>邀请码<input class="code-input" name="connection_code" inputmode="numeric" maxlength="9" pattern="[0-9]{9}" placeholder="输入电脑端的邀请码" required></label><button class="secondary-button" type="submit">申请使用</button></form></details></div>
    <div class="dashboard-grid"><section class="panel"><div class="panel-title"><div><h3>电脑列表</h3><p id="device-count" class="muted"></p></div><button class="secondary-button" id="refresh-devices">刷新</button></div><div id="device-list">正在加载电脑...</div></section><section id="device-detail" class="panel"></section></div>`;
  $("#pair-form").addEventListener("submit",pairDevice);
  $("#refresh-devices").addEventListener("click",()=>refreshDevices().catch(e=>notify(e.message,true)));
  await loadDevices();await loadRequests(state.selectedDeviceId);
  if(state.page!=="dashboard")return;
  renderDeviceList();renderDeviceDetail();
}
function renderDeviceList() {
  const list=$("#device-list");if(!list)return;
  $("#device-count").textContent=`我的电脑 ${state.devices.filter(d=>d.owner_id===state.user.id).length} / ${state.deviceLimit} 台`;
  list.innerHTML=state.devices.length?state.devices.map(d=>`<button type="button" class="device-row ${d.id===state.selectedDeviceId?"selected":""}" data-device-id="${d.id}"><div class="device-main"><div class="device-name">${esc(d.name)}</div><div class="device-meta">${esc(d.owner_name)}${state.devices.filter(x=>x.name===d.name).length>1?" · 电脑编号 "+d.id:""}<br>${esc(d.preset?.name||"尚未选择预设")}</div></div><div class="device-state">${statusLabel(d.online?"online":"offline")}</div></button>`).join(""):'<p class="muted">还没有电脑。请打开客户端，填写服务器地址并登录这个账号。</p>';
  list.querySelectorAll("[data-device-id]").forEach(n=>n.addEventListener("click",async()=>{
    saveDeviceDraft();state.selectedDeviceId=Number(n.dataset.deviceId);
    try {await loadRequests(state.selectedDeviceId);if(state.page==="dashboard"){renderDeviceList();renderDeviceDetail();}}catch(e){notify(e.message,true);}
  }));
}
function renderDeviceDetail() {
  const root=$("#device-detail"),device=state.devices.find(d=>d.id===state.selectedDeviceId);if(!root)return;
  saveDeviceDraft();
  if(!device){root.innerHTML='<div class="detail-empty">电脑登录后，在左侧选择它即可开始。</div>';return;}
  const active=state.requests.find(r=>r.device_id===device.id&&["waiting_capture","capturing","uploading","processing"].includes(r.status));
  const own=device.owner_id===state.user.id||state.user.role==="admin";
  const latest=state.requests.find(r=>r.device_id===device.id);
  root.innerHTML=`<div class="panel-title"><div><h3>${esc(device.name)}</h3>${statusLabel(device.online?"online":"offline")}</div><div class="toolbar"><button class="secondary-button" id="disconnect-device">断开连接</button><button class="danger-button" id="remove-device">${own?"删除电脑":"从列表移除"}</button></div></div>
    <div class="preset-summary"><strong>当前预设：${esc(device.preset?.name||"尚未选择")}</strong><p class="muted">${esc(device.preset?.description||"在这台电脑的客户端选择预设并保存，即可开始提问。")}</p></div>
    ${!device.preset_available?'<p class="request-status failed">请在客户端选择可用的预设并保存。</p>':""}
    ${!device.online?'<p class="muted">这台电脑尚未连接，请打开客户端并登录。</p>':""}
    <form id="request-form" data-device-id="${device.id}" class="control-grid"><label>这次想问什么（可不填）<textarea name="question" rows="3" maxlength="5000" placeholder="例如：请解释屏幕上这道题的解法"></textarea></label><div class="control-actions"><button class="primary-button" type="submit" ${device.online&&device.preset_available?"":"disabled"}>截图并提问</button><label class="checkbox-line"><input type="checkbox" name="force"> 停止上一条，重新提问</label>${active?'<button type="button" class="danger-button" id="cancel-active">停止回答</button>':""}</div></form>
    <div id="latest-request">${latest?requestResult(latest):'<div class="detail-empty">回答会显示在这里。</div>'}</div>`;
  restoreDeviceDraft();$("#request-form").addEventListener("submit",submitRequest);
  $("#disconnect-device").addEventListener("click",()=>deviceAction("disconnect"));
  $("#remove-device").addEventListener("click",()=>deviceAction(own?"delete":"unpair"));
  if($("#cancel-active"))$("#cancel-active").addEventListener("click",async()=>{
    try {await api("/api/requests/"+active.id+"/cancel",{method:"POST"});await loadRequests(device.id);renderDeviceDetail();}catch(e){notify(e.message,true);}
  });
}
async function pairDevice(e){e.preventDefault();try{var r=await api("/api/devices/pair",{method:"POST",body:JSON.stringify({connection_code:new FormData(e.currentTarget).get("connection_code")})});if(r.status==="approved"){notify("设备已授权");await loadDevices();renderDashboard();return;}notify("已发送授权请求，请在客户端确认");var id=r.pair_request_id,timer=setInterval(async function(){try{var x=await api("/api/pairings/"+id);if(x.status!=="pending"){clearInterval(timer);notify(x.status==="approved"?"客户端已授权":({rejected:"对方未同意",expired:"申请已过期"})[x.status]||"申请已结束",x.status!=="approved");await refreshDevices();}}catch(_){clearInterval(timer);}},1000);}catch(err){notify(err.message,true);}}
async function submitRequest(e) {
  e.preventDefault();const form=e.currentTarget,deviceId=state.selectedDeviceId,v=values(form),button=form.querySelector('[type="submit"]');
  button.disabled=true;
  try {
    const r=await api("/api/devices/"+deviceId+"/requests",{method:"POST",body:JSON.stringify({question:v.question,force:form.elements.force.checked})});
    const live=state.requests.find(row=>row.id===r.request.id);
    state.requests=state.requests.filter(row=>row.id!==r.request.id);state.requests.unshift(live||r.request);
    delete state.deviceDrafts[deviceId];form.reset();notify("正在截图，请稍候");
    if(state.page==="dashboard"&&state.selectedDeviceId===deviceId)renderDeviceDetail();
  }catch(err){notify(err.message,true);button.disabled=false;}
}
async function deviceAction(action) {
  const d=state.devices.find(x=>x.id===state.selectedDeviceId);if(!d)return;
  const message=action==="delete"?`删除“${d.name}”？这台电脑会退出登录，并释放一个电脑名额。已有回答会保留。`:action==="unpair"?"从列表中移除这台电脑？":"断开这台电脑？需要在客户端重新连接后才能继续提问。";
  if(!confirm(message))return;
  try {
    await api("/api/devices/"+d.id+(action==="delete"?"":"/"+action),{method:action==="delete"?"DELETE":"POST"});
    notify(action==="delete"?"电脑已删除，可以登录新电脑":action==="unpair"?"已移除":"已断开连接");
    await refreshDevices();
  }catch(err){notify(err.message,true);}
}
async function renderHistory(){contentRoot().innerHTML='<div class="page-intro"><div><span class="eyebrow">历史记录</span><h3>问答历史</h3></div><button id="refresh-history" class="secondary-button">刷新记录</button></div><section class="panel"><div id="history-list"><p class="muted">正在加载...</p></div></section>';$("#refresh-history").addEventListener("click",renderHistory);try{await loadRequests();var list=$("#history-list");if(!list)return;list.innerHTML=state.requests.length?state.requests.map(function(r){return '<article class="history-item" data-request-id="'+r.id+'"><div class="history-head"><strong>'+esc(r.device_name)+'</strong>'+statusLabel(r.status)+'</div><div class="history-question">'+esc(r.question||"未填写补充问题")+'</div><div class="muted">'+date(r.created_at)+' · '+esc(r.preset_name||"屏幕问答")+'</div></article>';}).join(""): '<p class="muted">暂无记录</p>';list.querySelectorAll("[data-request-id]").forEach(function(n){n.addEventListener("click",function(){var r=state.requests.find(function(x){return x.id===n.dataset.requestId;});if(r){modal('<h3>问答详情</h3>'+requestResult(r)+'<div class="modal-actions"><button class="secondary-button" onclick="closeModal()">关闭</button></div>');}});});}catch(err){notify(err.message,true);}}

async function renderPresets() {
  contentRoot().innerHTML=`<div class="page-intro"><div><h3>把常用设置保存为预设</h3><p class="muted">公共预设可以直接使用；也可以创建自己的预设。在客户端选择后即可提问。</p></div><button class="primary-button" id="new-preset">新建预设</button></div><section class="panel" id="preset-list">正在加载预设...</section>`;
  $("#new-preset").addEventListener("click",()=>presetModal());
  const result=await api("/api/presets");if(state.page!=="presets")return;state.presets=result.items;
  const list=$("#preset-list");
  list.innerHTML=state.presets.length?state.presets.map(p=>`<article class="preset-card"><div><h3>${esc(p.name)} <span class="role-label">${p.is_global?"公共预设":"我的预设"}</span></h3><p class="muted">${esc(p.description||"已保存完整的回答设置")}</p>${!p.enabled?'<p class="form-message error">已停用，无法用于新提问</p>':""}</div>${p.editable?`<div class="toolbar"><button class="secondary-button" data-edit-preset="${p.id}">编辑</button><button class="secondary-button" data-toggle-preset="${p.id}">${p.enabled?"停用":"启用"}</button></div>`:'<span class="muted">可在客户端选择使用</span>'}</article>`).join(""):'<p class="muted">还没有预设。点击新建预设，或请管理员提供公共预设。</p>';
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
    <label>希望怎样回答<textarea name="prompt" rows="4" maxlength="20000" placeholder="例如：先给出答案，再用简单的语言讲解步骤。" required>${esc(p?.prompt||"")}</textarea></label>
    <fieldset><legend>回答服务</legend><p class="muted">以下信息由你使用的人工智能服务商提供。保存一次后，日常使用只需选择预设。</p><div class="form-grid">
    <label>服务类型<select name="provider">${[["openai_chat","OpenAI（对话模式）"],["openai_responses","OpenAI（回答模式）"],["deepseek","DeepSeek（深度求索）"],["gemini","Gemini（谷歌）"]].map(([v,l])=>option(v,l,p?.provider||"openai_chat")).join("")}</select><small class="muted">前两项分别对应服务商提供的对话和回答接口。</small></label>
    <label>模型名称<input name="model_name" maxlength="160" value="${esc(p?.model_name||"")}" placeholder="填写服务商提供、支持识别图片的模型" required></label>
    <label class="full">服务地址<input name="endpoint" type="url" maxlength="1000" value="${esc(p?.endpoint||"")}" placeholder="复制服务商提供的完整调用地址" required></label>
    <label class="full">服务密钥${p?"（留空沿用已保存的密钥）":""}<input name="api_key" type="password" autocomplete="new-password" maxlength="4000" ${p?"":"required"}></label></div></fieldset>
    <details><summary>更多设置（通常无需修改）</summary><div class="form-grid"><label>思考程度<select name="reasoning_effort">${[["none","使用服务默认设置"],["low","简单思考"],["medium","适度思考"],["high","深入思考"],["xhigh","尽量深入思考"]].map(([v,l])=>option(v,l,p?.reasoning_effort||"none")).join("")}</select><small class="muted">谷歌服务选择默认时会关闭额外思考。</small></label>
    <label>最长等待时间（秒）<input name="timeout_seconds" type="number" min="10" max="600" value="${p?.timeout_seconds||120}"></label>
    <label>回答灵活程度（可留空）<input name="temperature" type="number" min="0" max="2" step="0.1" value="${esc(p?.options?.temperature??"")}"></label>
    <label>回答长度上限（可留空）<input name="output_limit" type="number" min="1" value="${esc(p?.options?.max_output_tokens??p?.options?.max_completion_tokens??p?.options?.max_tokens??"")}"></label></div></details>
    ${state.user.role==="admin"&&!p?'<label class="checkbox-line"><input name="is_global" type="checkbox"> 提供给所有人使用（公共预设）</label>':""}
    <p id="preset-message" class="form-message"></p><div class="modal-actions"><button class="secondary-button" type="button" onclick="closeModal()">取消</button><button class="primary-button" type="submit">保存预设</button></div></form>`);
  $("#preset-form").addEventListener("submit",async e=>{
    e.preventDefault();const form=e.currentTarget,v=values(form),button=form.querySelector('[type="submit"]');button.disabled=true;
    const options=Object.assign({},p?.options||{});delete options.temperature;delete options.max_tokens;delete options.max_completion_tokens;delete options.max_output_tokens;
    if(v.temperature!=="")options.temperature=Number(v.temperature);
    if(v.output_limit!=="")options[["gemini","openai_responses"].includes(v.provider)?"max_output_tokens":"max_tokens"]=Number(v.output_limit);
    const payload={name:v.name,description:v.description,prompt:v.prompt,provider:v.provider,endpoint:v.endpoint,api_key:v.api_key,model_name:v.model_name,reasoning_effort:v.reasoning_effort,timeout_seconds:Number(v.timeout_seconds),options,is_global:p?p.is_global:!!form.elements.is_global?.checked};
    try {await api(p?"/api/presets/"+p.id:"/api/presets",{method:p?"PUT":"POST",body:JSON.stringify(payload)});closeModal();notify("预设已保存，可在客户端刷新并选择");await renderPresets();}catch(err){setMessage("#preset-message",err.message,true);button.disabled=false;}
  });
}

async function renderUsers(){if(state.user.role!=="admin")return navigate("dashboard");contentRoot().innerHTML='<div class="page-intro"><div><span class="eyebrow">账号管理</span><h3>账号管理</h3></div><button class="secondary-button" id="refresh-users">刷新</button></div><section class="panel"><div id="user-table" class="data-table-wrap"><p class="muted">正在加载...</p></div></section>';$("#refresh-users").addEventListener("click",renderUsers);var rows=(await api("/api/admin/users")).items;$("#user-table").innerHTML='<table><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>注册备注</th><th>创建时间</th><th>操作</th></tr></thead><tbody>'+rows.map(function(r){return '<tr><td><strong>'+esc(r.display_name)+'</strong><div class="muted">'+esc(r.username)+'</div></td><td>'+(r.role==="admin"?"管理员":"普通用户")+'</td><td>'+statusLabel(r.status)+(r.locked_until?'<div class="muted">锁定至 '+date(r.locked_until)+'</div>':'')+'</td><td>'+esc(r.registration_note)+'</td><td>'+date(r.created_at)+'</td><td><div class="toolbar">'+(r.status==="pending"?'<button class="primary-button" data-user-action="approve" data-user-id="'+r.id+'">批准</button>':'')+(r.status==="active"&&r.id!==state.user.id?'<button class="danger-button" data-user-action="disable" data-user-id="'+r.id+'">禁用</button>':'')+(r.status==="disabled"?'<button class="secondary-button" data-user-action="enable" data-user-id="'+r.id+'">启用</button>':'')+'<button class="secondary-button" data-user-action="unlock" data-user-id="'+r.id+'">解锁</button><button class="secondary-button" data-user-action="reset" data-user-id="'+r.id+'">重置密码</button>'+(r.id!==state.user.id&&r.status!=="deleted"?'<button class="danger-button" data-user-action="delete" data-user-id="'+r.id+'">删除</button>':'')+'</div></td></tr>';}).join("")+'</tbody></table>';$("#user-table").querySelectorAll("[data-user-action]").forEach(function(b){b.addEventListener("click",function(){userAction(Number(b.dataset.userId),b.dataset.userAction);});});}
async function userAction(id,action){if(action==="delete"){if(!confirm("确定删除账号？"))return;try{await api("/api/admin/users/"+id,{method:"DELETE"});notify("账号已删除");renderUsers();}catch(err){notify(err.message,true);}return;}var body={};if(action==="approve"||action==="enable")body.status="active";if(action==="disable")body.status="disabled";if(action==="unlock")body.unlock=true;if(action==="reset"){var p=prompt("请输入新的临时密码");if(!p)return;body.reset_password=p;}try{await api("/api/admin/users/"+id,{method:"PATCH",body:JSON.stringify(body)});notify("账号已更新");renderUsers();}catch(err){notify(err.message,true);}}
async function renderAudit() {
  if(state.user.role!=="admin")return navigate("dashboard");
  const actions={"user.register":"申请注册", "auth.login":"登录账号", "auth.login_failed":"登录未成功", "auth.logout":"退出登录", "auth.password_changed":"修改密码", "device.pair_requested":"申请使用电脑", "device.unpaired":"取消电脑共享", "device.disconnected":"断开电脑", "request.created":"截图并提问", "request.cancelled":"停止回答", "user.updated":"更新账号设置", "user.deleted":"删除账号"};
  const targets={user:"账号",device:"电脑",request:"问答"};
  contentRoot().innerHTML='<div class="page-intro"><h3>操作记录</h3><button class="secondary-button" id="refresh-audit">刷新</button></div><section class="panel"><div id="audit-table" class="data-table-wrap">正在加载...</div></section>';
  $("#refresh-audit").addEventListener("click",renderAudit);
  const rows=(await api("/api/admin/audit-logs")).items;if(state.page!=="audit")return;
  $("#audit-table").innerHTML='<table><thead><tr><th>时间</th><th>账号</th><th>操作</th><th>对象</th><th>访问地址</th></tr></thead><tbody>'+rows.map(r=>`<tr><td>${date(r.created_at)}</td><td>${esc(r.username==="system"?"系统":r.username)}</td><td>${esc(actions[r.action]||"更新设置")}</td><td>${esc(targets[r.target_type]||"设置")} ${esc(r.target_id||"")}</td><td>${esc(r.ip_address||"")}</td></tr>`).join("")+'</tbody></table>';
}
function profileModal(){modal('<h3>账号设置</h3><p class="muted">当前账号：'+esc(state.user.username)+' · '+esc(state.user.display_name)+'</p><form id="password-form" class="stack-form"><label>当前密码<input name="current_password" type="password" required></label><label>新密码<input name="new_password" type="password" minlength="8" required></label><div class="modal-actions"><button type="button" class="secondary-button" onclick="closeModal()">取消</button><button class="primary-button" type="submit">修改密码</button></div></form>');$("#password-form").addEventListener("submit",async function(e){e.preventDefault();try{var result=await api("/api/auth/change-password",{method:"POST",body:JSON.stringify(values(e.currentTarget))});state.csrf=result.csrf_token;closeModal();notify("密码已修改");connectBrowser();}catch(err){notify(err.message,true);}});}
boot().catch(function(err){notify(err.message,true);});

