App.register('dashboard',{
title:'仪表盘',
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.dashboard.refresh()'),
async init(){this.renderShell();this.loadData();App.setInterval(()=>this.loadData(),30000);},
async refresh(){this.loadData();},
renderShell(){
App.renderBody(UI.statGrid([
UI.statCard('🟢', '...', '模块健康'),
UI.statCard('⚡', '...', 'API状态'),
UI.statCard('💬', '开始对话', '点击进入', 'App.navigate(\'chat\')'),
])+UI.card(UI.loading('加载中...'), '模块状态'));
},
async loadData(){try{
const[dash,health]=await Promise.all([API.getDashboard().catch(()=>null),API.getHealth().catch(()=>null)]);
const mods=dash?.data?.modules||{};const names=Object.keys(mods);
const ok=Object.values(mods).filter(v=>v==='healthy').length;
const hstat=health?.data?.status;
const rows=Object.entries(mods).map(([n,s])=>{
const bc=s==='healthy'?'green':s==='degraded'?'yellow':'red';
const lb=s==='healthy'?'正常':s==='degraded'?'降级':s;
return [`<strong>${UI.e(n)}</strong>`, UI.badge(lb, bc)];
});
const html=UI.statGrid([
UI.statCard('🟢', `${ok}/${names.length}`, '模块健康'),
UI.statCard('⚡', hstat==='healthy'?'健康':hstat||'-', 'API状态'),
UI.statCard('💬', '开始对话', '点击进入', 'App.navigate(\'chat\')'),
])+UI.card(UI.table(['模块','状态'], rows), '模块状态');
App.renderBody(html);
}catch{/* dashboard data unavailable */}},

async updateHealthFast(){try{
const r=await API.getHealth();
const d=r.data;document.getElementById('healthDot').className='status-dot '+(d.status==='healthy'?'online':'degraded');
document.getElementById('healthText').textContent=d.status==='healthy'?'系统健康':'系统降级';
}catch{/* health check failed */}},
destroy(){}
});
