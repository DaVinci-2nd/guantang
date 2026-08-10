(function () {
  const { ref } = Vue

  const SettingsPage = {
    template: document.getElementById('tpl-settings').innerHTML,
    setup() {
      const playerName = ref(store.player.name === 'Untitled' ? '' : store.player.name)
      const playerAvatar = ref(store.player.avatar.startsWith('p-') ? '' : store.player.avatar)
      const playerAvatarFile = ref(null)

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

      return { store, playerName, playerAvatar, playerAvatarFile, savePlayer, saveMultimodal, uploadPlayerAvatar, onPlayerAvatarFile, applyTheme: store.applyTheme, saveUi: store.saveUi }
    },
  }

  window.SettingsPage = SettingsPage
})()
