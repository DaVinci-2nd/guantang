(function () {
  const { createApp, computed } = Vue

  const GTApp = {
    template: document.getElementById('tpl-app').innerHTML,
    setup() {
      const pages = [
        { key: 'chat', label: '对话', icon: 'chat' },
        { key: 'roles', label: '角色', icon: 'role' },
        { key: 'skills', label: '技能', icon: 'skill' },
        { key: 'modes', label: '模式', icon: 'mode' },
        { key: 'models', label: '模型', icon: 'model' },
        { key: 'settings', label: '设置', icon: 'sliders' },
      ]
      const pageComponent = computed(() => {
        const map = {
          chat: window.ChatPage,
          roles: window.RolesPage,
          skills: window.SkillsPage,
          modes: window.ModesPage,
          models: window.ModelsPage,
          settings: window.SettingsPage,
        }
        return map[store.page] || window.ChatPage
      })

      async function toggleTheme() {
        store.theme = store.theme === 'dark' ? 'light' : 'dark'
        store.applyTheme()
        await store.saveUi()
      }

      async function toggleLeft() {
        store.leftOpen = !store.leftOpen
        await store.saveUi()
      }

      async function toggleRight() {
        store.rightOpen = !store.rightOpen
        await store.saveUi()
      }

      return { store, pages, pageComponent, toggleTheme, toggleLeft, toggleRight }
    },
  }

  async function bootstrap() {
    try {
      await store.loadState()
    } catch (e) {
      document.body.innerHTML = '<div style="padding:40px;font-family:sans-serif">无法连接后端服务，请确认 server.py 已启动</div>'
      return
    }
    const app = createApp(GTApp)
    app.component('icon', {
      props: ['name', 'size'],
      computed: {
        path() { return window.ICONS[this.name] || '' },
      },
      template: '<svg :width="size || 16" :height="size || 16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" v-html="path"></svg>',
    })
    app.component('chat-list', window.GTComponents.ChatList)
    app.component('right-role-list', window.GTComponents.RightRoleList)
    app.component('role-avatar', window.GTComponents.RoleAvatar)
    app.component('player-avatar', window.GTComponents.PlayerAvatar)
    app.component('modal', window.GTComponents.Modal)
    app.component('message-bubble', window.GTComponents.MessageBubble)
    app.component('chat-page', window.ChatPage)
    app.mount('#app')
  }

  bootstrap()
})()
