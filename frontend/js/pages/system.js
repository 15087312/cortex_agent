App.register('system',{
title:'系统信息',
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.system.refresh()'),
async init(){this.renderShell();this.loadData();},
async refresh(){this.renderShell();this.loadData();},
renderShell(){
let h=UI.statGrid([
UI.statCard('🖥', 'Cortex Agent', '系统'),
UI.statCard('🏷', '-', '版本'),
UI.statCard('🟡', '待连接', '状态'),
]);
h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">';
h+=UI.card('加载中...', '🧠 思维模块');
h+=UI.card('加载中...', '💾 数据库');
h+=UI.card('加载中...', '🔍 健康检查');
h+=UI.card('加载中...', '👁 注意力');
h+='</div>';App.renderBody(h);},
async loadData(){try{
const[infoR,thinkR,dbR,healthR,attR]=await Promise.all([
API.getSystemInfo().catch(()=>null),API.getThinkingStatus().catch(()=>null),
API.getDatabase().catch(()=>null),API.getHealth().catch(()=>null),
API.get('/attention/status').catch(()=>null)]);
const info=infoR?.data||{};const think=thinkR?.data||{};const db=dbR?.data||{};const health=healthR?.data||{};const att=attR?.data||{};
const mods=think.models||{};const checks=health.checks||{};
let h=UI.statGrid([
UI.statCard('🖥', info.name||'Cortex Agent', '系统'),
UI.statCard('🏷', info.version||'-', '版本'),
UI.statCard(health.status==='healthy'?'🟢':'🟡', health.status==='healthy'?'健康':health.status||'-', '状态'),
]);
h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">';
const tl='状态: '+UI.badge(think.status||'-', think.status==='healthy'?'green':'yellow');
const ml='大模型: '+UI.badge(mods.big?'可用':'不可用', mods.big?'green':'red')+'<br>中模型: '+UI.badge(mods.medium?'可用':'不可用', mods.medium?'green':'red')+'<br>小模型: '+UI.badge(mods.small?'可用':'不可用', mods.small?'green':'red');
h+=UI.card(tl+'<br>'+ml, '🧠 思维模块');
const tbls=db.tables||[];const cache=db.cache||{};
h+=UI.card(`类型: ${UI.e(db.type||'sqlite')}<br>表: ${tbls.length}<br>缓存命中: ${cache.hits||0}`, '💾 数据库');
let ch=Object.keys(checks).length===0?'暂无':Object.entries(checks).map(([n,s])=>`${UI.e(n)}: ${UI.badge(s, s==='ok'?'green':'red')}`).join('<br>');
h+=UI.card(ch, '🔍 健康检查');
h+=UI.card(`状态: ${att.status?UI.badge(att.status, att.status==='healthy'?'green':'red'):UI.badge('-','gray')}`, '👁 注意力');
h+='</div>';App.renderBody(h);
}catch{}},
destroy(){}
});
