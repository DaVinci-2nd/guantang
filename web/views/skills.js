(function () {
  const { ref } = Vue

  const SkillsPage = {
    template: document.getElementById('tpl-skills').innerHTML,
    setup() {
      const editor = ref(null)
      const argsText = ref('')

      function openEditor(skill) {
        if (skill) {
          editor.value = { ...skill, _orig: skill.name, args: [...(skill.args || [])] }
          argsText.value = (skill.args || []).join('\n')
        } else {
          editor.value = { name: '', type: 'mcp', command: '', args: [], env: null, enabled: true, provider: 'tavily', api_key: '', base_url: '', tool_name: '', url: '' }
          argsText.value = ''
        }
      }

      async function saveSkill() {
        const data = { ...editor.value }
        if (!data.name.trim()) { store.notify('名称不能为空'); return }
        data.args = argsText.value.split('\n').map((s) => s.trim()).filter(Boolean)
        if (data.type === 'search') {
          data.command = ''
          data.args = []
        }
        try {
          if (store.skills.some((s) => s.name === editor.value._orig)) {
            await api.put(`/api/skills/${encodeURIComponent(editor.value._orig)}`, data)
          } else {
            await api.post('/api/skills', data)
          }
          await store.loadState()
          editor.value = null
        } catch (e) {
          store.notify(e.message)
        }
      }

      async function removeSkill(name) {
        if (!confirm(`删除技能 ${name}？`)) return
        await api.del(`/api/skills/${encodeURIComponent(name)}`)
        await store.loadState()
      }

      return { store, editor, argsText, openEditor, saveSkill, removeSkill }
    },
  }

  window.SkillsPage = SkillsPage
})()
