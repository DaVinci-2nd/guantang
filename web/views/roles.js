(function () {
  const { ref, computed, watch } = Vue

  const RolesPage = {
    template: document.getElementById('tpl-roles').innerHTML,
    setup() {
      const editor = ref(null)
      const preview = ref('')
      const avatarFile = ref(null)
      const importCardFile = ref(null)
      const importCherryFile = ref(null)
      const importNativeFile = ref(null)

      const thinkingPreset = computed(() => {
        if (!editor.value || !editor.value.model) return null
        const name = editor.value.model.toLowerCase()
        return store.thinkingPresets.find((p) => p.match.some((k) => name.includes(k))) || null
      })

      function blankEditor() {
        return {
          name: '', avatar: '', model: '',
          thinking_mode: '', thinking_strength: 'medium', thinking_custom: '',
          temperature: 1.0, max_tokens: 4096,
          skills: [], modes: [], default_mode: '',
          setting: '',
        }
      }

      function openEditor(role) {
        editor.value = role ? { ...role, _orig: role.name, skills: [...(role.skills || [])], modes: [...(role.modes || [])] } : blankEditor()
        if (!editor.value.thinking_mode && thinkingPreset.value) {
          editor.value.thinking_mode = thinkingPreset.value.default || ''
        }
      }

      watch(thinkingPreset, (preset) => {
        if (!editor.value || !preset) return
        const valid = preset.options.map((o) => o.value)
        if (!editor.value.thinking_mode || !valid.includes(editor.value.thinking_mode)) {
          editor.value.thinking_mode = preset.default || ''
        }
      })

      async function saveRole() {
        const data = { ...editor.value }
        if (!data.name.trim()) { store.notify('名称不能为空'); return }
        try {
          if (store.roles.some((r) => r.name === editor.value._orig)) {
            await api.put(`/api/roles/${encodeURIComponent(editor.value._orig)}`, data)
          } else {
            await api.post('/api/roles', data)
          }
          await store.loadState()
          editor.value = null
        } catch (e) {
          store.notify(e.message)
        }
      }

      async function removeRole(name) {
        if (!confirm(`删除角色 ${name}？会话记录会保留`)) return
        await api.del(`/api/roles/${encodeURIComponent(name)}`)
        await store.loadState()
      }

      function uploadAvatar() {
        if (avatarFile.value) avatarFile.value.click()
      }

      async function onAvatarFile(e) {
        const file = e.target.files[0]
        if (!file) return
        try {
          const res = await api.upload(`/api/roles/${encodeURIComponent(editor.value.name)}/avatar`, file)
          editor.value.avatar = res.avatar
          e.target.value = ''
        } catch (err) {
          store.notify(err.message)
        }
      }

      async function previewPrompt() {
        try {
          const name = editor.value._orig || editor.value.name
          const res = await api.get(`/api/roles/${encodeURIComponent(name)}/system-prompt?mode=${encodeURIComponent(editor.value.default_mode || '')}`)
          preview.value = res.system_prompt || '（空）'
        } catch (e) {
          store.notify(e.message)
        }
      }

      function renderMarkdown(text) {
        try { return marked.parse(text || '') } catch (e) { return text || '' }
      }

      function clickImport(kind) {
        if (kind === 'card') importCardFile.value.click()
        else if (kind === 'cherry') importCherryFile.value.click()
        else importNativeFile.value.click()
      }

      async function onImportFile(e, url) {
        const file = e.target.files[0]
        e.target.value = ''
        if (!file) return
        try {
          const res = await api.upload(url, file)
          store.notify(`已导入角色：${res.name}`, 'ok')
          await store.loadState()
        } catch (err) {
          store.notify(err.message)
        }
      }

      async function exportRole(name) {
        try {
          await api.download(`/api/roles/${encodeURIComponent(name)}/export`, `${name}.png`)
          store.notify('已导出角色卡', 'ok')
        } catch (err) {
          store.notify(err.message)
        }
      }

      return { store, editor, preview, avatarFile, thinkingPreset, openEditor, saveRole, removeRole, uploadAvatar, onAvatarFile, previewPrompt, renderMarkdown, importCardFile, importCherryFile, importNativeFile, clickImport, onImportCard: (e) => onImportFile(e, '/api/roles/import-card'), onImportCherry: (e) => onImportFile(e, '/api/roles/import-cherry'), onImportNative: (e) => onImportFile(e, '/api/roles/import-native'), exportRole }
    },
  }

  window.RolesPage = RolesPage
})()
