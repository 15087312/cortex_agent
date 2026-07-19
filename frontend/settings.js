App.register('settings',{
title:'设置',
headerActions:UI.btnSm('⟳ 刷新', 'App._pages.settings.refresh()'),
async init(){await this.render();},
async refresh(){App.renderBody(UI.loading('刷新中...'));await this.render();},
async render(){
const key=API._key||'';
let h='<div style="max-width:720px;margin:0 auto">';

// API Key
h+=UI.card(`<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;line-height:1.6">用于访问需要认证的后端接口（如修改配置）。由后端 .env 中的 SIMPLE_API_KEY 控制。</div>
<div class="search-bar" style="margin-bottom:0">${UI.input('输入 X-API-Key', 'apiKeyInput', key)}
${UI.btnSm('保存', 'App._pages.settings.saveKey()','primary')}
${key?UI.btnSm('清除', 'App._pages.settings.clearKey()'):''}</div>
<div style="margin-top:8px;font-size:12px">${key?UI.badge('✓ 已配置','green'):'<span style="color:var(--text-muted)">未配置</span>'}</div>`, 'API 密钥');

// Runtime Config
try{const r=await API.getConfig();const cfg=r.data||r||{};const keys=Object.keys(cfg);
if(keys.length===0){h+=UI.card('<div style="color:var(--text-muted);font-size:13px;text-align:center;padding:12px 0">暂无配置项</div>', `运行时配置`);}
else{
const rows=keys.map(k=>{
const v=cfg[k];const vs=typeof v==='object'?JSON.stringify(v):String(v);
return [`<code style="font-size:12px">${UI.e(k)}</code>`, `<span style="font-family:var(--font-mono);font-size:13px;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">${UI.e(vs)}</span>`,
UI.btnSm('编辑', `App._pages.settings.editCfg('${UI.jsStr(k)}','${UI.jsStr(vs)}')`)];
});
h+=UI.card(UI.table(['配置键','当前值','操作'], rows), `运行时配置 (${keys.length}项) <span style="font-weight:400;font-size:12px;color:var(--text-muted)">修改后即时生效</span>`);
}
}catch{h+=UI.alert('加载配置需要 API Key','warning');}

// Model status
try{const mr=await API.getThinkingStatus();const ms=mr.data?.models||{};
const rows=[['large','总指挥','big'],['supervisor','主管','medium'],['expert','专家','small']].map(([k,lbl,bk])=>{
const ok=ms[k]||ms[bk];
return [lbl, `<span style="color:var(--text-muted)">${k}</span>`, UI.badge(ok?'可用':'不可用', ok?'green':'red')];
});
h+=UI.card(UI.table(['模型','角色','状态'], rows), '🤖 模型状态');
}catch{}

h+='</div>';App.renderBody(h);
},

saveKey(){const k=document.getElementById('apiKeyInput').value;API.setKey(k);App.showToast(k?'已保存':'已清除','success');this.refresh();},
clearKey(){API.setKey('');App.showToast('已清除','success');this.refresh();},
async editCfg(k,v){const nv=prompt('编辑 '+k,v);if(nv===null)return;
try{let val=nv;if(val==='true')val=true;else if(val==='false')val=false;else if(!isNaN(val)&&val.trim()!=='')val=Number(val);
await API.updateConfig(k,val);App.showToast(k+'已更新','success');this.refresh();}catch(e){App.showToast('更新失败:'+(e.body?.error?.message||e.status),'error')}},
destroy(){}
});