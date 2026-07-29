<script setup>
import { ref, onMounted } from 'vue'
import { getApiKey, setApiKey } from '@/api.js'
import { useConfigStore } from '@/stores/config.js'
import { useToastStore } from '@/stores/toast.js'

const toast = useToastStore()
const configStore = useConfigStore()
const keyInput = ref(getApiKey())

onMounted(async () => { await configStore.loadConfig(); await configStore.loadModelStatus() })

function saveKey() { setApiKey(keyInput.value); toast.show(keyInput.value ? '已保存' : '已清除', 'success') }
function clearKey() { keyInput.value = ''; setApiKey(''); toast.show('已清除', 'success') }
async function editConfig(k, v) {
  const nv = prompt('编辑 ' + k, String(v)); if (nv === null) return
  let val = nv; if (val === 'true') val = true; else if (val === 'false') val = false; else if (!isNaN(val) && val.trim() !== '') val = Number(val)
  try { await configStore.updateConfig(k, val); toast.show(k + '已更新', 'success') } catch (e) { toast.show('更新失败: ' + (e.body?.error?.message || e.status), 'error') }
}
</script>

<template>
  <div>
    <div class="page-header"><h2>⚙ 设置</h2></div>
    <div class="page-body" style="max-width:720px;margin:0 auto">
      <div class="card"><div class="card-header">API 密钥</div>
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;line-height:1.6">用于访问需要认证的后端接口。由后端 .env 中的 SIMPLE_API_KEY 控制。</div>
        <div class="search-bar" style="margin-bottom:0"><input class="input" v-model="keyInput" placeholder="输入 X-API-Key" style="flex:1" /><button class="btn btn-primary btn-sm" @click="saveKey">保存</button><button v-if="keyInput" class="btn btn-sm" @click="clearKey">清除</button></div>
        <div style="margin-top:8px;font-size:12px"><span v-if="getApiKey()" class="badge badge-green">✓ 已配置</span><span v-else style="color:var(--text-muted)">未配置</span></div>
      </div>
      <div class="card" style="margin-top:12px"><div class="card-header">运行时配置 ({{ Object.keys(configStore.config).length }}项)</div>
        <div v-if="Object.keys(configStore.config).length === 0" class="empty-state"><span class="empty-icon">📭</span><p class="empty-text">暂无配置项</p></div>
        <table v-else class="data-table"><thead><tr><th>配置键</th><th>当前值</th><th>操作</th></tr></thead><tbody><tr v-for="(v,k) in configStore.config" :key="k"><td><code style="font-size:12px">{{ k }}</code></td><td><span style="font-family:var(--font-mono);font-size:13px;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">{{ typeof v === 'object' ? JSON.stringify(v) : String(v) }}</span></td><td><button class="btn btn-sm" @click="editConfig(k, v)">编辑</button></td></tr></tbody></table>
      </div>
      <div class="card" style="margin-top:12px"><div class="card-header">🤖 模型状态</div>
        <table class="data-table"><thead><tr><th>模型</th><th>角色</th><th>状态</th></tr></thead><tbody><tr v-for="(bk,lbl) in {big:'总指挥',medium:'主管',small:'专家'}" :key="lbl"><td>{{ lbl }}</td><td style="color:var(--text-muted)">{{ bk === 'big' ? 'large' : bk === 'medium' ? 'supervisor' : 'expert' }}</td><td><span class="badge" :class="configStore.modelStatus[bk]||configStore.modelStatus[bk==='big'?'large':bk==='medium'?'supervisor':'expert']?'badge-green':'badge-red'">{{ configStore.modelStatus[bk]||configStore.modelStatus[bk==='big'?'large':bk==='medium'?'supervisor':'expert']?'可用':'不可用' }}</span></td></tr></tbody></table>
      </div>
    </div>
  </div>
</template>
