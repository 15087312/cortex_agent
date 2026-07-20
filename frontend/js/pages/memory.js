App.register('memory',{
title:'记忆管理',_filter:{type:'',keyword:'',limit:50},
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.memory.refresh()'),
async init(){this.renderShell();this.loadData();},
async refresh(){this.renderShell();this.loadData();},
renderShell(){
let h=UI.statGrid([
UI.statCard('', '-', '总事件'),
UI.statCard('', '-', '当前显示'),
UI.statCard('', UI.btnSm('清空记忆', 'App._pages.memory.clearAll()','danger'), '不可撤销'),
]);
h+=`<div class="search-bar">${UI.input('搜索关键词...','memKw',this._filter.keyword)}`;
h+=UI.select('memType',[['','全部'],['fact','fact'],['thought','thought'],['strategy','strategy'],['emotion','emotion']],this._filter.type);
h+=UI.btnSm('搜索','App._pages.memory.search()','primary');
h+=UI.btnSm('+新建','App._pages.memory.showCreate()');
h+='</div>';
h+=UI.card(UI.empty('加载中...','📭'), '记忆列表');
App.renderBody(h);},
async loadData(){try{
const r=await API.getMemoryEvents(this._filter.limit,this._filter.type,this._filter.keyword);
const evts=r.data.events||[];const total=r.data.total||0;
let h=UI.statGrid([
UI.statCard('', total, '总事件'),
UI.statCard('', evts.length, '当前显示'),
UI.statCard('', UI.btnSm('清空记忆', 'App._pages.memory.clearAll()','danger'), '不可撤销'),
]);
h+=`<div class="search-bar">${UI.input('搜索关键词...','memKw',this._filter.keyword)}`;
h+=UI.select('memType',[['','全部'],['fact','fact'],['thought','thought'],['strategy','strategy'],['emotion','emotion']],this._filter.type);
h+=UI.btnSm('搜索','App._pages.memory.search()','primary');
h+=UI.btnSm('+新建','App._pages.memory.showCreate()');
h+='</div>';
if(evts.length===0){h+=UI.card(UI.empty('暂无记忆','📭'));}
else{const rows=evts.map(e=>{
const tb=e.type==='fact'?'blue':e.type==='thought'?'green':e.type==='strategy'?'yellow':'gray';
return [UI.badge(e.type, tb), `<span style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">${UI.e(e.fact||'')}</span>`, (e.importance||0).toFixed(2), UI.time(e.time),
UI.btnSm('详情', `App._pages.memory.showDetail('${UI.jsStr(e.id)}')`)+' '+UI.btnSm('删除', `App._pages.memory.delEvent('${UI.jsStr(e.id)}')`,'danger')];
});
h+=UI.card(UI.table(['类型','内容','重要性','时间','操作'], rows));}
App.renderBody(h);
}catch{}},
search(){this._filter.keyword=document.getElementById('memKw').value;this._filter.type=document.getElementById('memType').value;this.refresh();},
async showDetail(id){try{
const r=await API.getMemoryEvent(id);const e=r.data;
const kw=(e.keywords||[]).map(k=>UI.badge(k,'gray')).join(' ');
const rows=[
['ID', `<span style="font-family:var(--font-mono);font-size:12px">${UI.e(e.id)}</span>`],
['类型', UI.badge(e.type,'blue')],
['事实', UI.e(e.fact||'-')],
['思考', UI.e(e.thought||'-')],
['重要性', (e.importance||0).toFixed(3)],
['关键词', kw||'-'],
['时间', UI.time(e.time)],
];
const html='<table style="width:100%;font-size:13px;line-height:1.8">'+rows.map(([k,v])=>`<tr><td style="width:80px;color:var(--text-muted)">${UI.e(k)}</td><td>${v}</td></tr>`).join('')+'</table>'
+UI.modalActions([UI.btn('关闭', "this.closest('.modal-overlay').remove()")]);
document.body.insertAdjacentHTML('beforeend', UI.modal(html, '记忆详情'));
}catch{App.showToast('加载详情失败','error')}},
async delEvent(id){if(!confirm('确定删除？不可撤销'))return;try{await API.deleteMemoryEvent(id);App.showToast('已删除','success');this.refresh();}catch{App.showToast('删除失败','error')}},
async clearAll(){if(!confirm('确定清空所有？不可撤销！'))return;try{await API.clearMemory();App.showToast('已清空','success');this.refresh();}catch{App.showToast('清空失败','error')}},
showCreate(){
const html=`<div style="display:flex;flex-direction:column;gap:12px">
<div><label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:4px">类型</label>${UI.select('newET',[['fact','fact'],['thought','thought'],['strategy','strategy'],['emotion','emotion']],'fact')}</div>
<div><label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:4px">内容 *</label>${UI.textarea('','newEF')}</div>
<div><label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:4px">关键词（逗号分隔）</label>${UI.input('','newEK')}</div>
<div><label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:4px">重要性 0-1</label><input class="input" id="newEI" type="number" min="0" max="1" step="0.1" value="0.5"></div>
</div>${UI.modalActions([UI.btn('取消', 'this.closest(\'.modal-overlay\').remove()'), UI.btnSm('创建', 'App._pages.memory.createEv()','primary')])}`;
document.body.insertAdjacentHTML('beforeend', UI.modal(html, '新建记忆'));
},
async createEv(){const f=document.getElementById('newEF').value;if(!f){App.showToast('请输入内容','error');return;}
try{await API.createMemoryEvent({fact:f,keywords:document.getElementById('newEK').value,importance:parseFloat(document.getElementById('newEI').value)||0.5,event_type:document.getElementById('newET').value});
App.showToast('已创建','success');document.querySelector('.modal-overlay')?.remove();this.refresh();}catch{App.showToast('创建失败','error')}},
destroy(){}
});