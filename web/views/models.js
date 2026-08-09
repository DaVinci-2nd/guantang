(function () {
  const { ref } = Vue

  const ModelsPage = {
    template: document.getElementById('tpl-models').innerHTML,
    setup() {
      const editor = ref(null)
      const showKey = ref(false)

      function openEditor(model) {
        editor.value = model ? { ...model, _orig: model.name } : {
          name: '', provider: 'openai',
          base_url: 'https://api.openai.com',
          model: '', api_key: '',
        }
        showKey.value = false
      }

      async function saveModel() {
        const data = { ...editor.value }
        if (!data.name.trim()) { store.notify('名称不能为空'); return }
        try {
          if (store.models.some((m) => m.name === editor.value._orig)) {
            await api.put(`/api/models/${encodeURIComponent(editor.value._orig)}`, data)
          } else {
            await api.post('/api/models', data)
          }
          await store.loadState()
          editor.value = null
        } catch (e) {
          store.notify(e.message)
        }
      }

      async function removeModel(name) {
        if (!confirm(`删除模型 ${name}？`)) return
        await api.del(`/api/models/${encodeURIComponent(name)}`)
        await store.loadState()
      }

      return { store, editor, showKey, openEditor, saveModel, removeModel }
    },
  }

  window.ModelsPage = ModelsPage
})()
