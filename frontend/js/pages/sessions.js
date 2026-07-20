App.register('sessions',{
title:'会话监控',
_selectedSession:null,
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.sessions.refresh()'),
async init(){this.renderShell();this.loadData();},
async refresh(){this.renderShell();this.loadData();},
renderShell(){
let h=UI.statGrid([
UI.statCard('', '-', '活跃会话'),
UI.statCard('💬', '新对话', '跳转聊天', 'App.navigate(\'chat\')'),
]);
h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">';
h+=`<div class="card" style="max-height:500px;overflow-y:auto"><div class="card-header">会话列表</div><div style="text-align:center;padding:40px;color:var(--text-muted)">加载中...</div></div>`;
h+=`<div class="card" id="sessionDialog" style="max-height:500px;overflow-y:auto"><div class="card-header">对话框</div><div style="text-align:center;padding:40px;color:var(--text-muted)">点击左侧查看</div></div></div>`;
App.renderBody(h);},
async loadData(){try{
const r=await API.getManagementSessions();const sessions=r.data?.sessions||[];
let h=UI.statGrid([
UI.statCard('', sessions.length, '活跃会话'),
UI.statCard('💬', '新对话', '跳转聊天', 'App.navigate(\'chat\')'),
]);
if(sessions.length===0){h+=UI.card(UI.empty('暂无活跃会话','📋'));App.renderBody(h);return;}
h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">';
h+=`<div class="card" style="max-height:500px;overflow-y:auto"><div class="card-header">会话列表 (${sessions.length})</div>`;
for(const s of sessions){const sid=s.session_id||'';const act=s.is_active||s.state==='active';const ds=s.dialog_size||0;
const bg=this._selectedSession===sid?'var(--accent-bg)':'';
const bl=this._selectedSession===sid?'3px solid var(--accent)':'3px solid transparent';
h+=`<div style="padding:8px 10px;cursor:pointer;border-radius:4px;margin-bottom:4px;background:${bg};border-left:${bl}" onclick="App._pages.sessions.select('${UI.jsStr(sid)}')">`;
h+=`<div style="display:flex;justify-content:space-between"><span style="font-size:13px;font-family:var(--font-mono)">${sid?sid.slice(0,12)+'...':'unknown'}</span>${UI.badge(act?'活跃':'非活跃', act?'green':'gray')}</div>`;
h+=`<div style="font-size:11px;color:var(--text-muted);margin-top:4px">对话框: ${ds}条</div></div>`;}
h+='</div><div class="card" id="sessionDialog" style="max-height:500px;overflow-y:auto"><div class="card-header">对话框</div><div style="text-align:center;padding:40px;color:var(--text-muted)">点击左侧查看</div></div></div>';
App.renderBody(h);
}catch{}},
async select(sid){this._selectedSession=sid;const el=document.getElementById('sessionDialog');if(!el)return;el.innerHTML=UI.loading('');
try{const r=await API.getSessionDialog(sid,50);const dialog=r.data?.dialog||[];
let h=`<div class="card-header">对话框 (${dialog.length}条)</div>`;
if(dialog.length===0)h+='<div style="text-align:center;padding:40px;color:var(--text-muted)">暂无消息</div>';
else for(const e of dialog){const role=e.role||e.sender||'system';const c=e.content||e.text||'';
const rl=role==='user'?'👤 用户':role==='assistant'||role==='large'?'🤖 AI':'● 系统';
const rc=role==='user'?'var(--accent)':role==='assistant'||role==='large'?'var(--success)':'var(--text-muted)';
h+=`<div style="padding:8px 10px;margin-bottom:6px;border-radius:6px;background:var(--bg-tertiary)"><div style="display:flex;justify-content:space-between;font-size:11px;color:${rc}"><span>${rl}</span><span style="color:var(--text-muted)">${UI.time(e.timestamp||e.time)}</span></div><div style="font-size:13px;line-height:1.5">${UI.e((c||'').slice(0,500))}</div></div>`;}
el.innerHTML=h;}catch{el.innerHTML=UI.alert('加载对话框失败')}},
destroy(){}
});