App.register('security',{
title:'安全审计',
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.security.refresh()'),
async init(){this.renderShell();this.loadData();},
async refresh(){this.renderShell();this.loadData();},
renderShell(){
let h=UI.card(UI.empty('安全策略加载后将显示于此','🔘'), '安全开关');
h+=UI.card(UI.empty('审计日志将在此显示','📋'), '审计日志');
App.renderBody(h);},
async loadData(){try{
const[sResp,aResp]=await Promise.all([API.getSecurityStatus().catch(()=>null),API.getSecurityAudit(50).catch(()=>null)]);
const state=sResp?.data?.state||{};const logs=aResp?.data?.logs||[];
const labels={'L0':'基础校验','L1':'内容审核','L2':'输出审查','L3':'工具安全','L4':'执行保护'};
let h=UI.card(Object.keys(state).length===0
?UI.empty('安全策略加载后将显示于此','🔘')
:Object.entries(state).map(([lv,en])=>{
const lb=labels[lv]||lv;
return `<span class="badge ${en?'badge-green':'badge-gray'}" style="padding:8px 12px;cursor:pointer;font-size:13px" onclick="App._pages.security.toggle('${UI.jsStr(lv)}',${!en})">${lb}: ${en?'●开':'○关'}</span>`;
}).join(''), '安全开关');
if(logs.length===0){h+=UI.card(UI.empty('审计日志将在此显示','📋'), '审计日志');}
else{const rows=logs.map(l=>{
const ok=l.passed||l.result===true;
return [UI.time(l.timestamp||l.time), UI.e(l.action||l.type||''), `<span style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">${UI.e((l.content||l.message||l.input||'').slice(0,80))}</span>`, UI.badge(ok?'通过':'拦截', ok?'green':'red')];
});
h+=UI.card(UI.table(['时间','操作','内容','结果'], rows));}
App.renderBody(h);
}catch{}},
async toggle(lv,en){try{await API.setSecuritySwitch(lv,en);App.showToast(`${lv}已${en?'开启':'关闭'}`,'success');this.refresh();}catch{App.showToast('切换失败','error')}},
destroy(){}
});