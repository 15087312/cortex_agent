App.register('tools',{
title:'工具管理',
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.tools.refresh()'),
async init(){this.renderShell();this.loadData();},
async refresh(){this.renderShell();this.loadData();},
renderShell(){
let h=UI.statGrid([
UI.statCard('', '-', '总工具'),
UI.statCard('', '-', '最近调用'),
UI.statCard('', '-', '来源分类'),
]);
h+=`<div class="search-bar">${UI.input('搜索工具...', 'toolSearch')}</div>`;
h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">';
h+=`<div class="card"><div class="card-header">工具列表</div>${UI.empty('加载中...','🔧')}</div>`;
h+=`<div class="card" id="toolDetail"><div class="card-header">工具详情</div>${UI.empty('选择一个工具查看详情','📋')}</div></div>`;
h+=UI.card(UI.empty('工具调用记录将显示在此','📋'), '调用历史');
App.renderBody(h);},
async loadData(){try{
const[tResp,eResp]=await Promise.all([API.getTools().catch(()=>null),API.getToolEvents(20).catch(()=>null)]);
const tools=tResp?.data?.tools||[];const evts=eResp?.data?.events||[];
const bySrc=tResp?.data?.by_source?Object.keys(tResp.data.by_source).length:0;
let h=UI.statGrid([
UI.statCard('', tools.length, '总工具'),
UI.statCard('', evts.length, '最近调用'),
UI.statCard('', bySrc, '来源分类'),
]);
h+=`<div class="search-bar">${UI.input('搜索工具...', 'toolSearch')}</div>`;
h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">';
if(tools.length===0){
  h+=`<div class="card"><div class="card-header">工具列表</div>${UI.empty('工具注册后自动出现于此','🔧')}</div>`;
}else{
  h+=`<div class="card" style="max-height:500px;overflow-y:auto"><div class="card-header">工具列表 (${tools.length})</div><div id="toolList">`;
  for(const t of tools){const n=t.name||t;
  h+=`<div class="tool-item" data-name="${UI.e(n)}" style="padding:8px 10px;cursor:pointer;border-radius:4px;font-size:13px" onclick="App._pages.tools.select('${UI.jsStr(n)}')">🔧 ${UI.e(n)}</div>`;}
  h+='</div></div>';
}
h+=`<div class="card" id="toolDetail"><div class="card-header">工具详情</div>${UI.empty('选择一个工具查看详情','📋')}</div></div>`;
if(evts.length>0){const rows=evts.map(e=>[UI.e(e.tool_name||e.name||''), UI.time(e.timestamp||e.time)]);
h+=UI.card(UI.table(['工具','时间'], rows), `调用历史 (${evts.length})`);}else{
h+=UI.card(UI.empty('工具调用记录将显示在此','📋'), '调用历史');}
App.renderBody(h);
}catch{}},
filter(){const q=(document.getElementById('toolSearch').value||'').toLowerCase();document.querySelectorAll('.tool-item').forEach(el=>{el.style.display=el.dataset.name.toLowerCase().includes(q)?'block':'none';});},
async select(n){const el=document.getElementById('toolDetail');if(!el)return;el.innerHTML=UI.loading('');
try{const r=await API.getToolInfo(n);const info=r.data;
let h=`<div class="card-header">${UI.e(n)}</div><div style="font-size:13px;line-height:1.8">`;
h+=`<div><span style="color:var(--text-muted)">描述:</span> ${UI.e(info.description||info.name||'-')}</div>`;
h+=`<div style="margin-top:8px"><span style="color:var(--text-muted)">来源:</span> ${UI.e(info.source||'builtin')}</div>`;
h+='<div style="margin-top:12px"><strong>调用工具</strong></div>';
h+=UI.textarea('{"path":"...","key":"value"}', 'toolParams');
h+=`<div style="margin-top:8px">${UI.btnSm('▶ 执行', `App._pages.tools.callTool('${UI.jsStr(n)}')`)}</div>`;
h+='<div id="toolResult" style="margin-top:8px"></div></div>';el.innerHTML=h;
}catch{el.innerHTML=UI.alert('加载详情失败')}},
async callTool(n){const el=document.getElementById('toolResult');if(!el)return;let p={};try{const r=document.getElementById('toolParams')?.value;if(r)p=JSON.parse(r);}catch{el.innerHTML=UI.alert('JSON格式错误');return;}
el.innerHTML=UI.spinner();try{const r=await API.callTool(n,p);el.innerHTML=UI.alert('成功','success')+'<pre style="background:var(--bg-tertiary);padding:8px;border-radius:4px;font-size:12px;overflow:auto;max-height:300px">'+UI.e(JSON.stringify(r.data,null,2))+'</pre>';}catch(e){el.innerHTML=UI.alert('失败: '+(e.body?.error?.message||e.status||'未知'));}},
destroy(){}
});