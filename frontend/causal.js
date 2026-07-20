App.register('causal',{
title:'因果图',
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.causal.refresh()'),
async init(){this.renderShell();this.loadData();},
async refresh(){this.renderShell();this.loadData();},
renderShell(){
let h=UI.statGrid([
UI.statCard('', '-', '节点'),
UI.statCard('', '-', '边'),
UI.statCard('', '-', '关联事件'),
UI.statCard('', '-', '已链接'),
]);
h+=UI.card(UI.empty('后端连接后将自动加载因果数据','🕸','记录事件之间的因果关系，帮助理解系统行为模式'), '因果节点');
h+='<div id="causalDetail"></div>';App.renderBody(h);},
async loadData(){try{
const r=await API.getCausalGraph();const d=r.data;const nodes=d.nodes||[];const edges=d.edges||[];const s=d.stats||{};
let h=UI.statGrid([
UI.statCard('', s.total_nodes||nodes.length, '节点'),
UI.statCard('', s.total_edges||edges.length, '边'),
UI.statCard('', s.total_events||0, '关联事件'),
UI.statCard('', s.linked_events||0, '已链接'),
]);
if(nodes.length>0){
const rows=nodes.map(n=>{
const tb=n.type==='root'?'green':n.type==='cause'?'yellow':'blue';
return [`<strong>${UI.e(n.label)}</strong>`, UI.badge(n.type, tb), (n.confidence||0).toFixed(2), n.event_count||0,
UI.btnSm('因果链', `App._pages.causal.showTree('${UI.jsStr(n.id)}')`)];
});
h+=UI.card(UI.table(['标签','类型','置信度','事件数','操作'], rows), '因果节点 <span style="font-weight:400;font-size:12px;color:var(--text-muted)">点击查看因果链</span>');
    }else{h+=UI.card(UI.empty('因果数据将在此显示','🕸'), '因果节点 <span style="font-weight:400;font-size:12px;color:var(--text-muted)">点击查看因果链</span>');}
h+='<div id="causalDetail"></div>';App.renderBody(h);
}catch{}},
async showTree(id){
const el=document.getElementById('causalDetail');if(!el)return;
el.innerHTML=UI.loading('');
try{
const[nr,tr]=await Promise.all([API.getCausalNode(id),API.getCausalTree(id,3)]);
const n=nr.data;const t=tr.data;
let h=UI.card(`<table class="data-table"><tr><td style="color:var(--text-muted)">节点</td><td>${UI.e(n?.node?.label||'')}</td></tr><tr><td style="color:var(--text-muted)">类型</td><td>${UI.e(n?.node?.type||'')}</td></tr></table>`, `因果链: ${UI.e(t?.anchor?.label||'')}`);
const preds=n?.predecessors||[];if(preds.length>0){
h+=`<div style="margin-top:12px"><strong style="font-size:13px;color:var(--text-muted)">⬆ 前驱节点</strong></div><div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">`;
h+=preds.map(p=>`<span class="badge badge-blue" style="cursor:pointer" onclick="App._pages.causal.showTree('${UI.jsStr(p.id)}')">${UI.e(p.label)}</span>`).join('')+'</div>';}
const succs=n?.successors||[];if(succs.length>0){
h+=`<div style="margin-top:12px"><strong style="font-size:13px;color:var(--text-muted)">⬇ 后继节点</strong></div><div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">`;
h+=succs.map(s=>`<span class="badge badge-green" style="cursor:pointer" onclick="App._pages.causal.showTree('${UI.jsStr(s.id)}')">${UI.e(s.label)}</span>`).join('')+'</div>';}
el.innerHTML=h;
}catch{el.innerHTML=UI.alert('加载因果链失败')}},
destroy(){}
});