App.register('modules',{
title:'模块管理',
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.modules.refresh()'),
async init(){this.renderShell();this.loadData();},
async refresh(){this.renderShell();this.loadData();},
renderShell(){
let h=UI.statGrid([
UI.statCard('', '-', '总模块'),
UI.statCard('', '-', '有API'),
UI.statCard('', '-', '有Core'),
]);
h+=UI.card(UI.empty('后端连接后将自动加载模块数据','🧩'), '模块列表');
App.renderBody(h);},
async loadData(){try{
const r=await API.getModules();const mods=r.data.modules||[];
let h=UI.statGrid([
UI.statCard('', mods.length, '总模块'),
UI.statCard('', r.data.with_api||0, '有API'),
UI.statCard('', r.data.with_core||0, '有Core'),
]);
const rows=mods.map(m=>{
const sc=m.status==='healthy'?'green':m.status==='degraded'?'yellow':'red';
return [
`<strong>${UI.e(m.name)}</strong>`,
m.has_api?UI.badge('✓','green'):UI.badge('✗','gray'),
m.has_core?UI.badge('✓','green'):UI.badge('✗','gray'),
UI.badge(m.status||'未知', sc),
UI.btnSm('刷新', `App._pages.modules.refreshMod('${UI.jsStr(m.name)}')`),
];
});
h+=UI.card(UI.table(['模块名','API','Core','状态','操作'], rows));
App.renderBody(h);
}catch{}},
async refreshMod(n){try{await API.refreshModule(n);App.showToast(n+'已刷新','success');this.render();}catch{App.showToast('刷新失败','error')}},
destroy(){}
});
