(function () {
  const { ref } = Vue

  const RolesPage = {
    template: document.getElementById('tpl-roles').innerHTML,
    setup() {
      const editor = ref(null)
      const preview = ref('')
      const avatarFile = ref(null)

      function blankEditor() {
        return {
          name: '', avatar: '', model: '',
          thinking_mode: 'chat', thinking_strength: 'medium',
          temperature: 1.0, max_tokens: 4096,
          skills: [], modes: [], default_mode: '',
          setting: '',
        }
      }

      function openEditor(role) {
        editor.value = role ? { ...role, _orig: role.name, skills: [...(role.skills || [])], modes: [...(role.modes || [])] } : blankEditor()
      }

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

      return { store, editor, preview, avatarFile, openEditor, saveRole, removeRole, uploadAvatar, onAvatarFile, previewPrompt }
    },
  }

  window.RolesPage = RolesPage
})()
