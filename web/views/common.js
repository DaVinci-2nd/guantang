(function () {
  const { reactive, ref, computed, watch, nextTick } = Vue

  const ChatList = {
    template: document.getElementById('tpl-chat').innerHTML,
    setup() {
      const filteredSessions = computed(() => {
        if (store.filterCharacter === '全部') return store.sessions
        return store.sessions.filter((s) => s.character_name === store.filterCharacter)
      })
      const filterOptions = computed(() => ['全部', ...store.sessionCharacters])
      async function renameSession(id) {
        const s = store.sessions.find((x) => x.id === id)
        if (!s) return
        const name = prompt('重命名会话', s.title || '')
        if (name === null) return
        const updated = await api.put(`/api/sessions/${id}`, { title: name.trim() })
        const idx = store.sessions.findIndex((x) => x.id === id)
        if (idx >= 0) store.sessions[idx] = updated
      }
      return { store, filteredSessions, filterOptions, renameSession, selectSession: store.selectSession, newSession: store.newSession }
    },
  }

  const RightRoleList = {
    template: document.getElementById('tpl-right-roles').innerHTML,
    setup() {
      function isActiveRole(role) {
        const s = store.currentSession()
        return !!s && s.character_name === role.name
      }
      async function pickRole(role) {
        if (!store.currentSessionId) {
          await store.newSession()
          if (store.currentSessionId) await store.switchSessionRole(role.name)
          return
        }
        await store.switchSessionRole(role.name)
      }
      return { store, isActiveRole, pickRole }
    },
  }

  const RoleAvatar = {
    template: document.getElementById('tpl-role-avatar').innerHTML,
    props: ['role', 'size'],
    computed: {
      avatarUrl() {
        const r = this.role || {}
        if (r.has_avatar_file && r.avatar) {
          return `/files/roles/${encodeURIComponent(r.name)}/${r.avatar}`
        }
        return ''
      },
    },
  }

  const PlayerAvatar = {
    template: document.getElementById('tpl-player-avatar').innerHTML,
    props: ['size'],
    setup() {
      return { player: store.player }
    },
  }

  const Modal = {
    template: document.getElementById('tpl-modal').innerHTML,
    props: ['title', 'wide'],
    emits: ['close'],
  }

  const MessageBubble = {
    template: document.getElementById('tpl-message-bubble').innerHTML,
    props: ['msg', 'streaming'],
    emits: ['regenerate', 'edit', 'preview'],
    setup() {
      return { store }
    },
    data() {
      return { showReasoning: false, showTools: false }
    },
    methods: {
      characterFor(msg) {
        const role = store.roles.find((r) => r.name === msg.character_name)
        if (role) return role
        return { name: msg.character_name || '角色', avatar: '', has_avatar_file: false }
      },
      formatTime(t) {
        if (!t) return ''
        const d = new Date(t * 1000)
        const pad = (n) => String(n).padStart(2, '0')
        return `${d.getMonth() + 1}-${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
      },
      renderMarkdown(text) {
        try { return marked.parse(text || '', { breaks: true }) } catch (e) { return text || '' }
      },
      prettyArgs(args) {
        if (!args) return ''
        try {
          const text = JSON.stringify(args, null, 2)
          return text.length > 500 ? text.slice(0, 500) + '…' : text
        } catch (e) {
          return String(args)
        }
      },
      copyText(msg) {
        navigator.clipboard.writeText(msg.content || '').then(
          () => store.notify('已复制', 'ok'),
          () => store.notify('复制失败')
        )
      },
      previewAttach(a) {
        if (a.kind === 'text' && a.content) {
          this.$emit('preview', { kind: 'text', name: a.name, content: a.content })
        }
      },
    },
  }

  const VariablesMenu = {
    template: document.getElementById('tpl-variables-menu').innerHTML,
    setup() {
      return { store }
    },
    data() {
      return { open: false }
    },
    computed: {
      groupOrder() {
        return ['context', 'datetime', 'hardware']
      },
    },
    methods: {
      groupItems(group) {
        return store.variables.filter((v) => v.group === group)
      },
      tokenText(item) {
        return item.keys.map((k) => '{{' + k + '}}').join(' / ')
      },
      insert(item) {
        const ta = this.$el.parentElement.querySelector('textarea')
        if (!ta) return
        const token = '{{' + item.keys[0] + '}}'
        const start = ta.selectionStart ?? ta.value.length
        const end = ta.selectionEnd ?? ta.value.length
        ta.setRangeText(token, start, end, 'end')
        ta.dispatchEvent(new Event('input'))
        ta.focus()
        const pos = start + token.length
        ta.setSelectionRange(pos, pos)
      },
    },
  }

  window.GTComponents = { ChatList, RightRoleList, RoleAvatar, PlayerAvatar, Modal, MessageBubble, VariablesMenu }
})()
