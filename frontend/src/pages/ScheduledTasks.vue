<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const sessions = ref([])
const selected = ref('')
const tasks = ref([])
const loading = ref(true)
const saving = ref(false)

async function loadSessions() {
  try {
    const r = await endpoints.sessions()
    sessions.value = (r.data || []).sort((a, b) => (b.last_active || '').localeCompare(a.last_active || ''))
    if (!selected.value && sessions.value.length) selected.value = sessions.value[0].session_id
    if (selected.value) await loadTasks()
  } catch {} finally { loading.value = false }
}

async function loadTasks() {
  if (!selected.value) return
  try {
    const r = await fetch('/stream/session/' + encodeURIComponent(selected.value) + '/tasks', { headers: { Accept: 'application/json' } })
    const d = await r.json()
    tasks.value = (d?.data?.tasks?.tasks) || []
  } catch { tasks.value = [] }
}

function addTask() {
  tasks.value.push({ id: 't' + Date.now(), time: '09:00', enabled: true, action: 'chat', prompt: '' })
}

function removeTask(i) { tasks.value.splice(i, 1) }

async function saveTasks() {
  saving.value = true
  try {
    const r = await fetch('/stream/session/' + encodeURIComponent(selected.value) + '/tasks', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tasks: { tasks: tasks.value } }),
    })
    const d = await r.json()
    if (d.success) toast.show('定时任务已保存', 'success')
    else toast.show('保存失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('保存失败', 'error') }
  finally { saving.value = false }
}

onMounted(loadSessions)
</script>

<template>
  <div>
    <div class="page-header">
      <h2>会话定时任务</h2>
      <button class="btn btn-sm" @click="loadSessions"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="card" v-if="!loading">
        <div class="card-header">选择会话（每会话定时任务独立）</div>
        <select v-model="selected" class="input" style="max-width:320px" @change="loadTasks">
          <option v-for="s in sessions" :key="s.session_id" :value="s.session_id">{{ s.title || s.session_id.slice(0, 16) }}</option>
        </select>
      </div>

      <div class="card" style="margin-top:12px" v-if="selected">
        <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
          <span>定时任务（到点调用逻辑 → 消息推送）</span>
          <button class="btn btn-sm btn-primary" @click="addTask"><Icon name="plus" :size="13" /> 添加任务</button>
        </div>

        <div v-if="tasks.length">
          <div v-for="(task, i) in tasks" :key="task.id" style="padding:12px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <input v-model="task.time" class="input" style="width:90px" placeholder="HH:MM" title="触发时间" />
              <label class="toggle-switch" title="启用">
                <input type="checkbox" v-model="task.enabled" /><span class="toggle-slider"></span>
              </label>
              <span style="font-size:12px;color:var(--text-muted)">启用</span>
              <select v-model="task.action" class="input" style="width:120px" title="触发的逻辑">
                <option value="chat">chat（LLM 消息）</option>
                <option value="pet">pet（桌宠）</option>
              </select>
              <button class="btn btn-sm danger" @click="removeTask(i)"><Icon name="trash" :size="13" /></button>
            </div>
            <textarea v-model="task.prompt" rows="2" class="input" style="margin-top:8px;font-size:13px" placeholder="可选提示词（留空用默认提醒语）"></textarea>
          </div>
        </div>
        <div v-else style="text-align:center;padding:24px;color:var(--text-muted)">暂无定时任务，点击"添加任务"创建</div>

        <div style="text-align:right;padding-top:12px">
          <button class="btn btn-sm btn-primary" :disabled="saving" @click="saveTasks">{{ saving ? '保存中...' : '保存任务' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
