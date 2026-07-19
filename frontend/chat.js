App.register('chat',{
title:'对话',
_sessionId:null,_messages:[],_processing:false,_sessionList:[],_connectPromise:null,
_streamingMsgIdx:-1,_streamingContent:'',_streamingTimer:null,
_attachments:[], // {type:'image', data:'data:...', name:'...'}  for multi-modal

headerActions:'',

    async init(){
        this._sessionId=null;this._messages=[];this._processing=false;
        await this.loadSessions();
        this.render();
        this.bindEvents();
        this.newSession();
        this._loadModelStatus();
    },
    async _loadModelStatus(){
        try{
            const r=await API.getThinkingStatus();
            const mods=r.data?.models||{};
            const el=document.getElementById('modelStatus');
            if(!el)return;
            const parts=[];
            if(mods.big||mods.large)parts.push(`🧠大${mods.big||mods.large?'✓':'✗'}`);
            if(mods.medium||mods.supervisor)parts.push(`📊中${mods.medium||mods.supervisor?'✓':'✗'}`);
            if(mods.small||mods.expert)parts.push(`🔧小${mods.small||mods.expert?'✓':'✗'}`);
            el.textContent=parts.length?'模型:'+parts.join(' '):'';
        }catch{}
    },

async loadSessions(){
try{const r=await API.getSessions();this._sessionList=r.data||r||[];if(!Array.isArray(this._sessionList))this._sessionList=[];}catch{this._sessionList=[];}
},

render(){
let h='<div style="display:flex;gap:0;height:100%">';

// Session list panel
h+='<div class="card" style="width:260px;flex-shrink:0;margin:0;border-radius:0;border:none;border-right:1px solid var(--border);overflow-y:auto;display:flex;flex-direction:column">';
h+='<div style="padding:16px;border-bottom:1px solid var(--border)"><h3 style="font-size:14px;color:var(--text-secondary);margin-bottom:12px">会话列表</h3>';
h+='<button class="btn btn-primary btn-sm" style="width:100%;justify-content:center" onclick="App._pages.chat.newSession()">+ 新建会话</button></div>';
h+='<div style="flex:1;overflow-y:auto;padding:8px" id="sessionListPanel">';
h+='</div></div>';

// Main chat area
h+='<div style="flex:1;display:flex;flex-direction:column;overflow:hidden">';

        // Chat header
        h+='<div class="chat-header" id="chatHeader">';
        h+='<div class="chat-header-left">';
        h+='<span class="chat-header-title" id="chatTitle" onclick="App._pages.chat.editTitle()" title="点击修改标题">新会话</span>';
        h+='<select class="input chat-model-select" id="modelSelect"><option value="large">总指挥</option><option value="supervisor">主管</option><option value="expert">专家</option></select>';
        h+='<span class="chat-model-status" id="modelStatus"></span>';
        h+='</div>';
        h+='<div class="chat-header-right">';
        h+='<button class="chat-btn-icon" onclick="App._pages.chat.copyLastCode()" title="复制最后一段代码">📋</button>';
        h+='<button class="chat-btn-icon" onclick="App._pages.chat.clearChat()" title="清空对话">🗑</button>';
        h+='</div></div>';

        // Messages area
        h+='<div class="chat-messages" id="chatMessages">';
        h+='<div class="chat-welcome"><div class="chat-welcome-icon">💬</div><div class="chat-welcome-text">开始新对话</div><div class="chat-welcome-hint">输入消息开始聊天</div></div>';
        h+='</div>';

        // Attachments preview
        h+='<div class="chat-attachments" id="chatAttachments" style="display:none"></div>';

        // Input area
        h+='<div class="chat-input-area">';
        h+='<div class="chat-input-wrapper">';
        h+='<div class="chat-input-toolbar">';
        h+='<button class="chat-btn-icon" id="chatAttachBtn" onclick="document.getElementById(\'chatFileInput\').click()" title="上传文件">📎</button>';
        h+='<input type="file" id="chatFileInput" multiple accept="image/*,.pdf,.txt" style="display:none" onchange="App._pages.chat.handleFiles(this.files)">';
        h+='</div>';
        h+='<textarea class="chat-input-field" id="chatInput" placeholder="输入消息... (Enter发送, Shift+Enter换行)" rows="1"></textarea>';
        h+='<div class="chat-input-actions">';
        h+='<span class="chat-input-hint" id="chatHint">Enter 发送 · Shift+Enter 换行</span>';
        h+='<button class="btn btn-sm" id="chatStopBtn" style="display:none;background:var(--danger);color:white;border-color:var(--danger)" onclick="App._pages.chat.stopThinking()">⏹ 停止</button>';
        h+='<button class="chat-send-btn" id="chatSendBtn" onclick="App._pages.chat.sendMessage()">➤</button>';
        h+='</div></div></div>';

h+='</div></div>';

App.renderBody(h);
this._renderSessionList();
},

    // ── 多模态：文件处理 ──
    handleFiles(files) {
        if (!files || files.length === 0) return;
        for (const file of files) {
            if (file.type.startsWith('image/')) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    this._addAttachment({ type: 'image', data: e.target.result, name: file.name });
                };
                reader.readAsDataURL(file);
            } else if (file.size < 1024 * 1024) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    this._addAttachment({ type: 'file', data: e.target.result, name: file.name });
                };
                reader.readAsDataURL(file);
            } else {
                App.showToast('文件过大（最大 1MB）', 'warning');
            }
        }
    },
    _addAttachment(att) {
        this._attachments.push(att);
        this._renderAttachments();
    },
    _removeAttachment(index) {
        this._attachments.splice(index, 1);
        this._renderAttachments();
    },
    _renderAttachments() {
        const el = document.getElementById('chatAttachments');
        if (!el) return;
        if (this._attachments.length === 0) {
            el.style.display = 'none';
            el.innerHTML = '';
            return;
        }
        el.style.display = 'flex';
        el.innerHTML = this._attachments.map((att, i) => {
            if (att.type === 'image') {
                return `<div class="chat-attachment-item">
                    <img src="${att.data}" class="chat-attachment-thumb">
                    <button class="chat-attachment-remove" onclick="App._pages.chat._removeAttachment(${i})">✕</button>
                </div>`;
            }
            return `<div class="chat-attachment-item chat-attachment-file">
                <span>📄 ${UI.e(att.name)}</span>
                <button class="chat-attachment-remove" onclick="App._pages.chat._removeAttachment(${i})">✕</button>
            </div>`;
        }).join('');
    },

    _renderSessionList(){
const el=document.getElementById('sessionListPanel');
if(!el)return;
let h='';
if(this._sessionList.length===0){
h='<div class="chat-sessions-empty">暂无会话</div>';
}else{
for(const s of this._sessionList){
const sid=s.session_id||'';
const title=s.title||sid.slice(0,12);
const time=s.last_active||s.created_at||'';
const msgCount=s.message_count||0;
const isActive=sid===this._sessionId;
const timeLabel=time?time.slice(5,16):'';
const bgStyle=isActive?'var(--accent-bg)':'transparent';
const borderStyle=isActive?'3px solid var(--accent)':'3px solid transparent';
h+=`<div class="session-item" data-sid="${sid.replace(/"/g,'&quot;')}" style="padding:10px 12px;border-radius:8px;cursor:pointer;margin-bottom:4px;background:${bgStyle};border-left:${borderStyle};transition:background 0.15s" title="左键切换，右键删除">`;
h+=`<div style="font-size:13px;color:var(--text-primary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${App.escapeHtml(title.slice(0,30))}</div>`;
h+=`<div style="font-size:11px;color:var(--text-muted);margin-top:4px">${timeLabel} · ${msgCount}条</div></div>`;
}
}
el.innerHTML=h;
},

// Enhanced Markdown rendering with highlight.js
_md(t){
if(!t)return '';
// Use marked for full markdown parsing if available
if(typeof marked !== 'undefined'){
try{
const html = marked.parse(t, {
breaks: true,
gfm: true,
highlight: function(code, lang){
if(lang && hljs.getLanguage(lang)){
try{return hljs.highlight(code,{language:lang}).value;}catch(e){}
}
try{return hljs.highlightAuto(code).value;}catch(e){}
return App.escapeHtml(code);
}
});
// Wrap code blocks with copy button and header
return html.replace(/<pre><code class="language-(\w+)">([\s\S]*?)<\/code><\/pre>/g,
'<div class="code-block"><div class="code-block-header"><span class="lang-label">$1</span><button class="copy-btn" onclick="App._pages.chat._copyCode(this)">📋 复制</button></div><pre><code class="hljs language-$1">$2</code></pre></div>'
).replace(/<pre><code>([\s\S]*?)<\/code><\/pre>/g,
'<div class="code-block"><div class="code-block-header"><span class="lang-label">代码</span><button class="copy-btn" onclick="App._pages.chat._copyCode(this)">📋 复制</button></div><pre><code class="hljs">$1</code></pre></div>'
).replace(/<code>([^<]+)<\/code>/g,'<code class="inlines-code">$1</code>');
}catch(e){/* fallback */}
}
// Fallback: basic markdown
let h = App.escapeHtml(t);
h = h.replace(/```(\w*)\n?([\s\S]*?)```/g,'<div class="code-block"><div class="code-block-header"><span class="lang-label">$1</span><button class="copy-btn" onclick="App._pages.chat._copyCode(this)">📋 复制</button></div><pre><code class="hljs">$2</code></pre></div>');
h = h.replace(/`([^`]+)`/g,'<code class="inlines-code">$1</code>');
h = h.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
h = h.replace(/^### (.+)$/gm,'<h3>$1</h3>');
h = h.replace(/^## (.+)$/gm,'<h2>$1</h2>');
h = h.replace(/^# (.+)$/gm,'<h1>$1</h1>');
h = h.replace(/\n/g,'<br>');
return h;
},

_copyCode(btn){
// Find code from adjacent pre element
const pre = btn.closest('.code-block')?.querySelector('pre code');
const code = pre?.textContent || '';
if(!code)return;
navigator.clipboard.writeText(code).then(()=>{
btn.textContent = '✅ 已复制';
btn.classList.add('copied');
setTimeout(()=>{btn.textContent = '📋 复制';btn.classList.remove('copied');},2000);
}).catch(()=>{
const ta = document.createElement('textarea');
ta.value = code; document.body.appendChild(ta); ta.select();
document.execCommand('copy'); document.body.removeChild(ta);
btn.textContent = '✅ 已复制';
setTimeout(()=>{btn.textContent = '📋 复制';},2000);
});
},

copyLastCode(){
const msgs = document.getElementById('chatMessages');
if(!msgs)return;
const codes = msgs.querySelectorAll('.code-block pre code');
if(codes.length===0){App.showToast('没有找到代码块','warning');return;}
const lastCode = codes[codes.length-1];
navigator.clipboard.writeText(lastCode.textContent||'').then(()=>{
App.showToast('已复制最后一段代码','success');
});
},

clearChat(){
if(!confirm('确定清空当前对话？'))return;
const m=document.getElementById('chatMessages');
if(m)m.innerHTML='<div class="chat-welcome"><div class="chat-welcome-icon">💬</div><div class="chat-welcome-text">对话已清空</div><div class="chat-welcome-hint">输入消息开始新对话</div></div>';
this._messages=[];
this._attachments=[];
this._renderAttachments();
},

// Message rendering with actions
_renderMsg(msg, index){
const r=msg.role||'ai';const c=msg.content||'';
const isU=r==='user';const avatar=isU?'U':'AI';const cls=isU?'user':'ai';
const content=isU?UI.e(c).replace(/\n/g,'<br>'):this._md(c);
const idx = index !== undefined ? index : this._messages.length-1;
const actions = `<button onclick="App._pages.chat.copyMsg(${idx})" title="复制">📋</button>
${isU?'':`<button onclick="App._pages.chat.deleteMsg(${idx})" title="删除">🗑</button>`}`;
return `<div class="message ${cls}" data-idx="${idx}"><div class="message-avatar">${avatar}</div><div class="message-bubble">${content}<div class="message-actions">${actions}</div></div></div>`;
},
// Lightweight message shell for streaming
_renderMsgShell(msgIndex){
return `<div class="message ai" data-idx="${msgIndex}"><div class="message-avatar">AI</div><div class="message-bubble"><div class="streaming-cursor">▊</div></div></div>`;
},
// Lightweight message shell for typing effect (no content)
_renderMsgShell(msgIndex){
return `<div class="message ai" data-idx="${msgIndex}"><div class="message-avatar">AI</div><div class="message-bubble"></div></div>`;
},

copyMsg(idx){
const m = this._messages[idx];
if(!m||!m.content)return;
navigator.clipboard.writeText(m.content).then(()=>{
App.showToast('已复制','success');
});
},

deleteMsg(idx){
if(idx < 0 || idx >= this._messages.length)return;
this._messages.splice(idx,1);
const el = document.getElementById('chatMessages');
if(el){
el.innerHTML = this._messages.length===0
?'<div class="empty-state" style="padding:60px"><div class="empty-icon">💬</div><p>开始新对话吧</p></div>'
:this._messages.map((m,i)=>this._renderMsg(m,i)).join('');
}
},

// Typing effect: gradually reveal AI message content
_typeMessage(content, msgIndex){
const el = document.getElementById('chatMessages');
if(!el)return;
// If marked is available, pre-render markdown for accurate rendering
// but reveal text gradually by increasing visible chars
const fullHtml = this._md(content);
const msgEl = el.querySelector(`.message[data-idx="${msgIndex}"]`);
if(!msgEl)return;
const bubble = msgEl.querySelector('.message-bubble');
if(!bubble)return;

// Remove any existing actions during typing
// Replace bubble content with plain text for typing effect
const plainText = content;
let pos = 0;
const speed = 18; // ms per character
const chunkSize = 3; // characters per tick

// First show the bubble with inline-code rendered parts visible
// Actually, for typing effect, simpler approach: render full markdown at end
// but gradually reveal char by char of the source text in a pre/code look

// Simpler: show rendered html char by char via a cursor reveal
// Actually the cleanest approach: just type out the plain text then swap to rendered

bubble.innerHTML = '';
const cursorSpan = document.createElement('span');
cursorSpan.className = 'typing-cursor';
cursorSpan.textContent = '▊';

const typeTimer = setInterval(() => {
pos += chunkSize;
if(pos >= plainText.length){
clearInterval(typeTimer);
// Replace with full rendered HTML
bubble.innerHTML = fullHtml + this._actionHtml(msgIndex);
// Re-highlight code blocks
bubble.querySelectorAll('pre code').forEach(block => {
try{hljs.highlightElement(block);}catch(e){}
});
// Scroll to bottom
this.scrollBottom();
return;
}
// Show plain text progressively with cursor
const shown = plainText.slice(0, pos);
bubble.innerHTML = App.escapeHtml(shown) + '<span style="animation:blink 0.8s infinite;color:var(--accent)">▊</span>';
this.scrollBottom();
}, speed);

// Store timer for cleanup
msgEl._typeTimer = typeTimer;
},

_actionHtml(idx){
return `<div class="message-actions">
<button onclick="App._pages.chat.copyMsg(${idx})" title="复制">📋</button>
<button onclick="App._pages.chat.deleteMsg(${idx})" title="删除">🗑</button>
</div>`;
},

scrollBottom(){const e=document.getElementById('chatMessages');if(e)setTimeout(()=>{e.scrollTop=e.scrollHeight},50);},

bindEvents(){
document.addEventListener('keydown',this._keyHandler=e=>{
if(e.key==='Enter'&&!e.shiftKey){const i=document.getElementById('chatInput');if(i&&document.activeElement===i){e.preventDefault();this.sendMessage();}}
});

// Image paste
document.addEventListener('paste',this._pasteHandler=e=>{
const items=e.clipboardData?.items;
if(!items)return;
for(const item of items){
if(item.type.startsWith('image/')){
e.preventDefault();
const file=item.getAsFile();
if(file)this.handleFiles([file]);
return;
}
}
});

// Drag-drop files on input area
document.addEventListener('dragover',this._dragOverHandler=e=>{
const ta=document.getElementById('chatInput');
if(!ta||!ta.closest('.chat-input-area'))return;
if(!e.dataTransfer.types.includes('Files'))return;
e.preventDefault();
ta.closest('.chat-input-wrapper').classList.add('drag-over');
});
document.addEventListener('dragleave',this._dragLeaveHandler=e=>{
document.querySelector('.chat-input-wrapper')?.classList.remove('drag-over');
});
document.addEventListener('drop',this._dropHandler=e=>{
const wrapper=document.querySelector('.chat-input-wrapper');
wrapper?.classList.remove('drag-over');
const files=e.dataTransfer?.files;
if(files&&files.length>0&&wrapper){
e.preventDefault();
this.handleFiles(files);
}
});

// Session list event delegation
document.addEventListener('click',this._sessionClickHandler=e=>{
const item = e.target.closest('.session-item');
if(!item)return;
const sid = item.dataset.sid;
if(sid) App._pages.chat.switchSession(sid);
});
document.addEventListener('contextmenu',this._sessionCtxHandler=e=>{
const item = e.target.closest('.session-item');
if(!item)return;
e.preventDefault();
const sid = item.dataset.sid;
if(sid) App._pages.chat.deleteSession(sid);
});

WS.on('thinking',this._onTh=d=>{
const e=document.getElementById('chatMessages');if(!e)return;
e.querySelector('.chat-welcome')?.remove();
e.querySelector('.empty-state')?.remove();
// Streaming content chunk: type=thinking, event=thinking_step, role=large, content=...
if(d.event==='thinking_step' && d.content){
this._streamingContent+=d.content;
if(this._streamingMsgIdx===-1){
const m={role:'assistant',content:''};
this._messages.push(m);
this._streamingMsgIdx=this._messages.length-1;
e.insertAdjacentHTML('beforeend',this._renderMsgShell(this._streamingMsgIdx));
}
// Debounced update: throttle rendering to ~60fps
if(!this._streamingTimer){
this._streamingTimer=setTimeout(()=>{
this._streamingTimer=null;
this._updateStreamingBubble();
},16);
}
return;
}
// Regular thinking indicator (no streaming content)
if(document.getElementById('thinkingBubble'))return;
const div=document.createElement('div');div.id='thinkingBubble';div.className='message ai';
div.innerHTML='<div class="message-avatar">AI</div><div class="message-bubble"><div class="thinking-indicator"><div class="thinking-dot"></div><span>正在思考</span><span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span></div></div>';
e.appendChild(div);this.scrollBottom();
});

WS.on('status',this._onPr=d=>{
const info=d.data||{};const el=document.getElementById('thinkingBubble');
if(el){const s=el.querySelector('span');if(s)s.textContent=`正在思考 ${info.elapsed_s||0}s`;}
});

WS.on('message',this._onMsg=d=>{
document.getElementById('thinkingBubble')?.remove();
document.querySelector('.chat-welcome')?.remove();
const content=d.content||'';
if(this._streamingMsgIdx>=0){
this._finalizeStreaming(content);
}else{
// No streaming events arrived — use fallback render
const e=document.getElementById('chatMessages');if(!e)return;
const m={role:'assistant',content:content};
this._messages.push(m);
const msgIndex=this._messages.length-1;
e.insertAdjacentHTML('beforeend',this._renderMsgShell(msgIndex));
this.scrollBottom();
// Fallback typewriter effect
this._typeMessage(content,msgIndex);
}
});

WS.on('done',this._onDone=()=>{
this._processing=false;
const s=document.getElementById('chatSendBtn'),st=document.getElementById('chatStopBtn');
if(s)s.style.display='';if(st)st.style.display='none';
const h=document.getElementById('chatHint');if(h)h.textContent='Enter 发送 | Shift+Enter 换行';
document.getElementById('thinkingBubble')?.remove();
// Finalize any leftover streaming (edge case)
if(this._streamingMsgIdx>=0)this._finalizeStreaming('');
});

WS.on('error',this._onErr=d=>{
this._processing=false;
const s=document.getElementById('chatSendBtn'),st=document.getElementById('chatStopBtn');
if(s)s.style.display='';if(st)st.style.display='none';
App.showToast('错误:'+(d.content||'未知'),'error');
});

WS.on('ack',d=>{
if(d.event==='busy'){
const h=document.getElementById('chatHint');if(h)h.textContent='思考中...';
setTimeout(()=>{WS.send({type:'input',content:App._pages.chat._lastMsg});},2000);
}
});
},

_updateStreamingBubble(){
const e=document.getElementById('chatMessages');
if(!e||this._streamingMsgIdx<0)return;
const msgEl=e.querySelector(`.message[data-idx="${this._streamingMsgIdx}"]`);
if(!msgEl)return;
const bubble=msgEl.querySelector('.message-bubble');
if(!bubble)return;
// Show accumulated plain text with cursor
bubble.innerHTML=App.escapeHtml(this._streamingContent)
+'<span style="animation:blink 0.8s infinite;color:var(--accent)">▊</span>';
this.scrollBottom();
},

_finalizeStreaming(content){
if(this._streamingTimer){clearTimeout(this._streamingTimer);this._streamingTimer=null;}
const e=document.getElementById('chatMessages');
if(this._streamingMsgIdx>=0&&e){
const msgEl=e.querySelector(`.message[data-idx="${this._streamingMsgIdx}"]`);
if(msgEl){
const bubble=msgEl.querySelector('.message-bubble');
if(bubble){
const finalContent=content||this._streamingContent;
const fullHtml=this._md(finalContent);
bubble.innerHTML=fullHtml+this._actionHtml(this._streamingMsgIdx);
bubble.querySelectorAll('pre code').forEach(block=>{
try{hljs.highlightElement(block);}catch(_){}
});
}
}
if(this._messages[this._streamingMsgIdx]){
this._messages[this._streamingMsgIdx].content=content||this._streamingContent;
}
}
this._streamingMsgIdx=-1;
this._streamingContent='';
this.scrollBottom();
},

async newSession(){
if(this._sessionId){WS.disconnect();}
try {
    const r = await API.createSession();
    this._sessionId = r.data.session_id;
} catch {
    this._sessionId = 'session_'+Date.now()+'_'+Math.random().toString(36).slice(2,8);
}
this._messages=[];this._processing=false;
this._attachments=[];this._renderAttachments();
this._connectPromise=WS.connect(this._sessionId);
document.getElementById('chatTitle').textContent='新会话';
const m=document.getElementById('chatMessages');if(m)m.innerHTML='<div class="chat-welcome"><div class="chat-welcome-icon">💬</div><div class="chat-welcome-text">新会话已创建</div><div class="chat-welcome-hint">选择一个模型，输入消息开始吧</div></div>';
this._renderSessionList();
},

switchSession(sid){
if(sid===this._sessionId)return;
if(this._sessionId)WS.disconnect();
this._sessionId=sid;this._messages=[];
this.loadSessionDialog(sid);
this._renderSessionList();
},

async loadSessionDialog(sid){
try{const r=await API.getSessionDialog(sid,100);const dialog=r.data?.dialog||[];
this._messages=dialog.map(d=>({role:d.role||d.sender||'assistant',content:d.content||d.text||''}));
const m=document.getElementById('chatMessages');if(m){
m.innerHTML=this._messages.length===0
?'<div class="chat-welcome"><div class="chat-welcome-icon">💬</div><div class="chat-welcome-text">暂无消息</div></div>'
:this._messages.map((msg,i)=>this._renderMsg(msg,i)).join('');
setTimeout(()=>{
m.querySelectorAll('pre code').forEach(block=>{
try{if(window.hljs)hljs.highlightElement(block);}catch(e){}
});
this.scrollBottom();
},100);
}
const titleEl=document.getElementById('chatTitle');
if(titleEl)titleEl.textContent=(sid.slice(0,12)+'...');
}catch{App.showToast('加载失败','error')};
this._renderSessionList();
},

// Title editing
editTitle(){
const el=document.getElementById('chatTitle');
if(!el)return;
const current=el.textContent.trim();
const input=document.createElement('input');
input.className='edit-title-input';
input.type='text';
input.value=current;
input.maxLength=50;
el.textContent='';
el.appendChild(input);
input.focus();
input.select();
const done=()=>{
const val=input.value.trim()||'新会话';
el.textContent=val;
};
input.addEventListener('blur',done);
input.addEventListener('keydown',e=>{
if(e.key==='Enter'){input.blur();}
if(e.key==='Escape'){done();}
});
},

async sendMessage(){
if(this._processing)return;
const i=document.getElementById('chatInput');if(!i)return;const t=i.value.trim();if(!t)return;
    this._lastMsg=t;
        if(!this._sessionId||!WS.connected){
            this.newSession();
            try{await this._connectPromise}catch{await new Promise(r=>setTimeout(r,2000));}
        }
        const modelSel=document.getElementById('modelSelect');
        const model=modelSel?modelSel.value:'large';
        // Include attachments in message
        const attachments=this._attachments.length>0?this._attachments.map(a=>a.data):undefined;
        const sent=WS.send({type:'input',content:t,model:model,attachments:attachments});
if(!sent){await new Promise(r=>setTimeout(r,1000));WS.send({type:'input',content:t,model:model,attachments:attachments});}
const m={role:'user',content:t};this._messages.push(m);
this._attachments=[];this._renderAttachments();
const el=document.getElementById('chatMessages');if(el){el.querySelector('.empty-state')?.remove();el.insertAdjacentHTML('beforeend',this._renderMsg(m, this._messages.length-1));this.scrollBottom();}
this._processing=true;i.value='';i.style.height='auto';
const s=document.getElementById('chatSendBtn'),st=document.getElementById('chatStopBtn');
if(s)s.style.display='none';if(st)st.style.display='';
const h=document.getElementById('chatHint');if(h)h.textContent='思考中...';
},

stopThinking(){
WS.send({type:'stop'});this._processing=false;
const s=document.getElementById('chatSendBtn'),st=document.getElementById('chatStopBtn');
if(s)s.style.display='';if(st)st.style.display='none';
const h=document.getElementById('chatHint');if(h)h.textContent='已停止';
// Finalize streaming if active
if(this._streamingMsgIdx>=0)this._finalizeStreaming('');
// Stop typing effect (fallback)
const msgs=document.getElementById('chatMessages');
if(msgs){
msgs.querySelectorAll('.message').forEach(el=>{
if(el._typeTimer){clearInterval(el._typeTimer);el._typeTimer=null;}
});
}
},

deleteSession(sid){
if(!confirm('确定删除此会话？'))return;
// Currently no backend API for delete, just remove from UI list
this._sessionList = this._sessionList.filter(s => s.session_id !== sid);
if(sid === this._sessionId) this.newSession();
this._renderSessionList();
},

destroy(){
WS.off('thinking',this._onTh);WS.off('status',this._onPr);WS.off('message',this._onMsg);
WS.off('done',this._onDone);WS.off('error',this._onErr);WS.off('ack',this._onBusy);
if(this._keyHandler)document.removeEventListener('keydown',this._keyHandler);
if(this._pasteHandler)document.removeEventListener('paste',this._pasteHandler);
if(this._dragOverHandler)document.removeEventListener('dragover',this._dragOverHandler);
if(this._dragLeaveHandler)document.removeEventListener('dragleave',this._dragLeaveHandler);
if(this._dropHandler)document.removeEventListener('drop',this._dropHandler);
if(this._sessionClickHandler)document.removeEventListener('click',this._sessionClickHandler);
if(this._sessionCtxHandler)document.removeEventListener('contextmenu',this._sessionCtxHandler);
// Clear streaming timer
if(this._streamingTimer){clearTimeout(this._streamingTimer);this._streamingTimer=null;}
// Clear any running type timers
const msgs=document.getElementById('chatMessages');
if(msgs){
msgs.querySelectorAll('.message').forEach(el=>{
if(el._typeTimer){clearInterval(el._typeTimer);el._typeTimer=null;}
});
}
}
});
