App.register('perception',{
title:'感知系统',
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.perception.refresh()'),
async init(){this.renderShell();this.refreshStatus();App.setInterval(()=>this.refreshStatus(),10000);},
async refresh(){this.renderShell();this.refreshStatus();},
renderShell(){
let h=UI.statGrid([
UI.statCard('⏸', '待启动', '状态'),
UI.statCard('🖥', '检测中...', '平台'),
UI.statCard('🎤', '不可用', '语音'),
]);
h+=UI.card(`<div style="display:flex;gap:8px">
${UI.btnSm('▶ 启动', 'App._pages.perception.startPer()', 'primary')}
${UI.btnSm('⏹ 停止', 'App._pages.perception.stopPer()', '')}
</div>`, '控制');
const b=document.getElementById('pageBody');
if(b&&!b.querySelector('.chat-welcome'))App.renderBody(h);},
async refreshStatus(){try{
const r=await API.getPerceptionStatus().catch(()=>null);const d=r?.data||{};
if(!d||Object.keys(d).length===0)return;
const run=d.status==='running';
let h=UI.statGrid([
UI.statCard(run?'🟢':'⏸', run?'运行中':'待启动', '状态'),
UI.statCard('🖥', UI.e(d.platform||'检测中...'), '平台'),
UI.statCard('🎤', d.voice_available?'可用':'不可用', '语音'),
]);
const pipe=d.pipeline||{};
if(pipe&&Object.keys(pipe).length>0){
const rows=Object.entries(pipe).map(([step,st])=>{
const ok=st==='ok'||st===true||st==='healthy';
return [UI.e(step), UI.badge(ok?'正常':'异常', ok?'green':'red')];
});
h+=UI.card(UI.table(['步骤','状态'], rows), '流水线');
}
h+=UI.card(`<div style="display:flex;gap:8px">
${UI.btnSm('▶ 启动', 'App._pages.perception.startPer()', 'primary')}
${UI.btnSm('⏹ 停止', 'App._pages.perception.stopPer()', '')}
</div>`, '控制');
const b=document.getElementById('pageBody');
if(b&&!b.querySelector('.chat-welcome'))App.renderBody(h);
}catch{}},
async startPer(){try{await API.startPerception();App.showToast('已启动','success');this.refreshStatus();}catch{App.showToast('启动失败','error')}},
async stopPer(){try{await API.stopPerception();App.showToast('已停止','success');this.refreshStatus();}catch{App.showToast('停止失败','error')}},
destroy(){}
});