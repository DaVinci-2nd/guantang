(function () {
  const { ref } = Vue

  const ModesPage = {
    template: document.getElementById('tpl-modes').innerHTML,
    setup() {
      const editor = ref(null)

      function openEditor(mode) {
        editor.value = mode ? { ...mode, _orig: mode.name } : { name: '', description: '', content: '' }
      }

      async function saveMode() {
        const data = { ...editor.value }
        if (!data.name.trim()) { store.notify('名称不能为空'); return }
        try {
          if (store.modes.some((m) => m.name === editor.value._orig)) {
            await api.put(`/api/modes/${encodeURIComponent(editor.value._orig)}`, data)
          } else {
            await api.post('/api/modes', data)
          }
          await store.loadState()
          editor.value = null
        } catch (e) {
          store.notify(e.message)
        }
      }

      async function removeMode(name) {
        if (!confirm(`删除模式 ${name}？`)) return
        await api.del(`/api/modes/${encodeURIComponent(name)}`)
        await store.loadState()
      }

      return { store, editor, openEditor, saveMode, removeMode }
    },
  }

  window.ModesPage = ModesPage
})()
