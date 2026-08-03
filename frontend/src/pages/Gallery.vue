<script setup>
/**
 * Gallery page — displays uploaded images in a responsive grid.
 * Supports: upload, lightbox preview, and toggleable filename display.
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useConfigStore } from '@/stores/config.js'
import { useToastStore } from '@/stores/toast.js'

const configStore = useConfigStore()
const toast = useToastStore()

const images = ref([])
const loading = ref(false)
const previewSrc = ref(null)
const uploadRef = ref(null)

const showFilenames = computed(
  () => !!configStore.config?.show_filename_in_gallery,
)

function _url(name) {
  return `/api/gallery/image/${encodeURIComponent(name)}`
}

function _fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

async function fetchImages() {
  loading.value = true
  try {
    const res = await fetch('/api/gallery/images')
    if (!res.ok) throw res
    const data = await res.json()
    images.value = data.images || []
  } catch {
    images.value = []
  } finally {
    loading.value = false
  }
}

async function handleUpload(e) {
  const file = e.target.files?.[0]
  if (!file) return

  const form = new FormData()
  form.append('file', file)
  try {
    const res = await fetch('/api/gallery/upload', { method: 'POST', body: form })
    if (!res.ok) throw res
    await fetchImages()
    toast.show('上传成功', 'success')
  } catch {
    toast.show('上传失败', 'error')
  } finally {
    // Reset input so same file can be re-uploaded
    if (uploadRef.value) uploadRef.value.value = ''
  }
}

function openPreview(name) {
  previewSrc.value = _url(name)
}

function closePreview() {
  previewSrc.value = null
}

function handleKeydown(e) {
  if (e.key === 'Escape') closePreview()
}

onMounted(() => {
  fetchImages()
  document.addEventListener('keydown', handleKeydown)
})
onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div class="gallery-page">
    <div class="gallery-toolbar">
      <h1>图库</h1>
      <label class="upload-btn">
        <input
          ref="uploadRef"
          type="file"
          accept="image/*"
          style="display:none"
          @change="handleUpload"
        />
        <span>上传</span>
      </label>
    </div>

    <div v-if="loading" class="gallery-empty">加载中...</div>

    <div v-else-if="!images.length" class="gallery-empty">
      暂无图片，点击「上传」添加
    </div>

    <div v-else class="gallery-grid">
      <div
        v-for="img in images"
        :key="img.name"
        class="gallery-item"
        @click="openPreview(img.name)"
      >
        <img
          :src="_url(img.name)"
          :alt="img.name"
          loading="lazy"
          class="gallery-thumb"
        />
        <div v-if="showFilenames" class="gallery-info">
          <span class="gallery-name">{{ img.name }}</span>
          <span class="gallery-size">{{ _fmtSize(img.size) }}</span>
        </div>
      </div>
    </div>

    <!-- Lightbox -->
    <Teleport to="body">
      <div v-if="previewSrc" class="lightbox" @click="closePreview">
        <img :src="previewSrc" class="lightbox-img" @click.stop />
        <button class="lightbox-close" @click="closePreview">×</button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.gallery-page { padding: 24px 32px; height: 100%; overflow-y: auto; }
.gallery-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.gallery-toolbar h1 { font-size: 20px; font-weight: 600; color: var(--text-primary, #e6edf3); margin: 0; }

.upload-btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 20px; border-radius: 6px; cursor: pointer;
  background: var(--accent, #58a6ff); color: #fff; font-size: 14px; font-weight: 500;
  transition: filter .15s;
}
.upload-btn:hover { filter: brightness(1.15); }

.gallery-empty {
  display: flex; align-items: center; justify-content: center;
  height: 200px; color: var(--text-muted, #8b949e); font-size: 14px;
}

.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}
.gallery-item {
  border-radius: 8px; overflow: hidden; cursor: pointer;
  background: var(--surface, #161b22);
  transition: transform .15s, box-shadow .15s;
}
.gallery-item:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,.25); }

.gallery-thumb {
  width: 100%; aspect-ratio: 1; object-fit: cover; display: block;
}

.gallery-info { padding: 6px 8px; }
.gallery-name {
  display: block; font-size: 12px; color: var(--text-primary, #e6edf3);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.gallery-size { font-size: 11px; color: var(--text-muted, #8b949e); }

.lightbox {
  position: fixed; inset: 0; z-index: 10000;
  background: rgba(0,0,0,.85);
  display: flex; align-items: center; justify-content: center;
}
.lightbox-img { max-width: 90vw; max-height: 90vh; object-fit: contain; }
.lightbox-close {
  position: absolute; top: 20px; right: 28px;
  background: none; border: none; color: #fff; font-size: 36px; cursor: pointer; line-height: 1;
}
</style>
