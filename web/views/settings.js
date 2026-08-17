(function () {
  const { ref } = Vue

  const SettingsPage = {
    template: document.getElementById('tpl-settings').innerHTML,
    setup() {
      const playerName = ref(store.player.name === 'Untitled' ? '' : store.player.name)
      const playerAvatar = ref(store.player.avatar.startsWith('p-') ? '' : store.player.avatar)
      const playerAvatarFile = ref(null)
      const builtinTools = ref([])
      const approvalRejectText = ref('')
      const settingsFile = ref(null)
      const importConfirm = ref(null)
      const importSummary = ref(null)
      const importStep2Open = ref(false)
      const importKeyword = ref('')

      async function loadBuiltinTools() {
        try {
          const data = await api.get('/api/builtin-tools')
          approvalRejectText.value = data.approval_reject_text || ''
          builtinTools.value = (data.tools || []).map((t) => ({
            key: t.key,
            name: t.name,
            approval: !!t.approval,
            description: t.description || '',
            parameters: Object.entries(t.parameters || {}).map(([n, p]) => ({
              name: n,
              type: (p && p.type) || 'string',
              description: (p && p.description) || '',
              required: (t.required || []).includes(n),
            })),
          }))
        } catch (e) {
          builtinTools.value = []
        }
      }

      function addParam(tool) {
        tool.parameters.push({ name: '', type: 'string', description: '', required: false })
      }

      function removeParam(tool, index) {
        tool.parameters.splice(index, 1)
      }

      async function saveBuiltinTools() {
        const tools = builtinTools.value.map((t) => ({
          key: t.key,
          name: t.name.trim(),
          approval: !!t.approval,
          description: t.description,
          parameters: t.parameters.map((p) => ({
            name: p.name.trim(),
            type: p.type,
            description: p.description,
            required: !!p.required,
          })),
        }))
        try {
          await api.put('/api/builtin-tools/form', { tools, approval_reject_text: approvalRejectText.value })
          await loadBuiltinTools()
          store.notify('已保存，内置工具配置立即生效', 'ok')
        } catch (e) {
          store.notify(e.message)
        }
      }

      loadBuiltinTools()

      async function savePlayer() {
        try {
          const payload = {
            player: { name: playerName.value || '', avatar: playerAvatar.value || '' },
          }
          await api.put('/api/config', payload)
          await store.loadState()
          store.notify('已保存', 'ok')
        } catch (e) {
          store.notify(e.message)
        }
      }

      async function saveMultimodal() {
        try {
          await api.put('/api/config', { multimodal: { ...store.multimodal } })
          await store.loadState()
          store.notify('已保存', 'ok')
        } catch (e) {
          store.notify(e.message)
        }
      }

      async function saveAutoTitle() {
        try {
          await api.put('/api/config', { auto_title: { ...store.autoTitle } })
          await store.loadState()
          store.notify('已保存', 'ok')
        } catch (e) {
          store.notify(e.message)
        }
      }

      function uploadPlayerAvatar() {
        if (playerAvatarFile.value) playerAvatarFile.value.click()
      }

      async function onPlayerAvatarFile(e) {
        const file = e.target.files[0]
        if (!file) return
        try {
          const form = new FormData()
          form.append('file', file)
          const resp = await fetch('/api/player/avatar', { method: 'POST', body: form })
          if (!resp.ok) throw new Error('上传失败')
          const res = await resp.json()
          playerAvatar.value = res.avatar
          e.target.value = ''
        } catch (err) {
          store.notify(err.message)
        }
      }

      async function exportSettings() {
        try {
          await api.download('/api/settings/export', 'guantang_settings.json')
          store.notify('已导出全局设置', 'ok')
        } catch (e) {
          store.notify(e.message)
        }
      }

      async function onSettingsFile(e) {
        const file = e.target.files[0]
        e.target.value = ''
        if (!file) return
        try {
          const data = JSON.parse(await file.text())
          importConfirm.value = data
          importSummary.value = {
            config: !!data.config,
            models: Array.isArray(data.models) ? data.models.length : 0,
            skills: Array.isArray(data.skills) ? data.skills.length : 0,
            modes: Array.isArray(data.modes) ? data.modes.length : 0,
            roles: Array.isArray(data.roles) ? data.roles.length : 0,
            builtin_tools: !!data.builtin_tools,
            player_avatar: !!data.player_avatar,
          }
          importKeyword.value = ''
        } catch (err) {
          store.notify('文件不是有效的全局设置 JSON')
        }
      }

      function importStep2() {
        importStep2Open.value = true
      }

      async function doImportSettings() {
        try {
          const res = await api.post('/api/settings/import', importConfirm.value)
          store.notify('已导入：' + (res.covered || []).join('、'), 'ok')
          await store.loadState()
          importConfirm.value = null
          importStep2Open.value = false
        } catch (e) {
          store.notify(e.message)
        }
      }

      return { store, playerName, playerAvatar, playerAvatarFile, savePlayer, saveMultimodal, saveAutoTitle, uploadPlayerAvatar, onPlayerAvatarFile, builtinTools, approvalRejectText, addParam, removeParam, saveBuiltinTools, applyTheme: store.applyTheme, saveUi: store.saveUi, settingsFile, importConfirm, importSummary, importStep2Open, importKeyword, exportSettings, onSettingsFile, importStep2, doImportSettings }
    },
  }

  window.SettingsPage = SettingsPage
})()
