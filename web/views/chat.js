(function () {
  const { reactive, ref, computed, watch, nextTick } = Vue

  let keySeq = 0

  const ChatPage = {
    template: document.getElementById('tpl-chat-page').innerHTML,
    setup() {
      const draft = ref('')
      const chatList = ref(null)
      const attachments = ref([])
      const fileInput = ref(null)

      async function onFiles(e) {
        const files = [...e.target.files]
        e.target.value = ''
        for (const f of files) {
          const item = { name: f.name, size: f.size, readable: false, content: '' }
          if (f.size <= 2 * 1024 * 1024) {
            try {
              const buf = await f.arrayBuffer()
              new TextDecoder('utf-8', { fatal: true }).decode(buf)
              item.content = new TextDecoder('utf-8').decode(buf)
              item.readable = true
            } catch (err) {
              item.readable = false
            }
          }
          attachments.value.push(item)
        }
      }

      const filteredSessions = computed(() => {
        if (store.filterCharacter === '全部') return store.sessions
        return store.sessions.filter((s) => s.character_name === store.filterCharacter)
      })

      const modeOptions = computed(() => {
        const role = store.currentRole()
        return role && role.modes ? role.modes : []
      })

      const filterOptions = computed(() => ['全部', ...store.sessionCharacters])

      function scrollBottom() {
        nextTick(() => {
          if (chatList.value) chatList.value.scrollTop = chatList.value.scrollHeight
        })
      }

      function currentRole() {
        return store.currentRole()
      }

      function currentSession() {
        return store.currentSession()
      }

      async function newSession() {
        await store.newSession()
        scrollBottom()
      }

      async function removeSession(id) {
        if (!confirm('删除这个会话？')) return
        await api.del(`/api/sessions/${id}`)
        if (store.currentSessionId === id) {
          store.currentSessionId = null
          store.messages = []
        }
        await store.loadSessions()
      }

      async function clearSession() {
        if (!confirm('清空当前会话的所有消息？')) return
        await api.del(`/api/sessions/${store.currentSessionId}/messages`)
        store.messages = []
      }

      function formatTime(t) {
        if (!t) return ''
        const d = new Date(t * 1000)
        const pad = (n) => String(n).padStart(2, '0')
        return `${d.getMonth() + 1}-${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
      }

      function renderMarkdown(text) {
        try { return marked.parse(text || '') } catch (e) { return text || '' }
      }

      async function onModeChange(e) {
        await store.switchSessionMode(e.target.value)
      }

      async function send() {
        const text = draft.value.trim()
        if ((!text && !attachments.value.length) || store.streaming) return
        const sessionId = store.currentSessionId
        if (!sessionId) return
        let content = text
        if (attachments.value.length) {
          const parts = attachments.value.map((a) =>
            a.readable ? `【附件：${a.name}】\n${a.content}` : `【附件：${a.name}】（二进制文件，内容无法直接读取）`
          )
          content = text ? text + '\n\n' + parts.join('\n\n') : parts.join('\n\n')
        }
        const meta = attachments.value.map((a) => ({ name: a.name, size: a.size, readable: a.readable }))
        draft.value = ''
        attachments.value = []
        store.streaming = true
        let saved
        try {
          saved = await api.post(`/api/sessions/${sessionId}/messages`, { content, attachments: meta })
        } catch (e) {
          store.streaming = false
          store.notify(e.message)
          return
        }
        saved.key = 'p' + keySeq++
        store.messages.push(saved)

        const aiMsg = reactive({
          key: 'a' + keySeq++,
          sender: 'character',
          character_name: store.currentSession().character_name,
          content: '',
          reasoning: '',
          tool_events: [],
          streaming: true,
          created_at: Date.now() / 1000,
        })
        store.messages.push(aiMsg)
        scrollBottom()

        const ws = new WebSocket(`ws://${location.host}/ws/chat`)
        ws.onmessage = (ev) => {
          const data = JSON.parse(ev.data)
          if (data.type === 'reasoning') {
            aiMsg.reasoning += data.delta
            scrollBottom()
          } else if (data.type === 'text') {
            aiMsg.content += data.delta
            scrollBottom()
          } else if (data.type === 'warn') {
            aiMsg.reasoning += `\n[中文守则] ${data.text}\n`
          } else if (data.type === 'tool_call') {
            aiMsg.tool_events.push({ name: data.name, arguments: data.arguments, result: '' })
          } else if (data.type === 'tool_exec') {
            const last = aiMsg.tool_events[aiMsg.tool_events.length - 1]
            if (last) last.result = '执行中…'
            scrollBottom()
          } else if (data.type === 'tool_result') {
            const last = aiMsg.tool_events[aiMsg.tool_events.length - 1]
            if (last) last.result = data.text
            scrollBottom()
          } else if (data.type === 'error') {
            aiMsg.content = aiMsg.content || ''
            store.notify(data.text)
            aiMsg.streaming = false
            store.streaming = false
            ws.close()
          } else if (data.type === 'end') {
            aiMsg.streaming = false
            aiMsg.id = data.message.id
            aiMsg.created_at = data.message.created_at
            aiMsg.tool_events = data.message.tool_events
            aiMsg.reasoning = data.message.reasoning
            aiMsg.content = data.message.content
            store.streaming = false
            ws.close()
            store.loadSessions().catch(() => {})
            scrollBottom()
          }
        }
        ws.onerror = () => {
          store.notify('连接失败')
          aiMsg.streaming = false
          store.streaming = false
        }
        ws.onopen = () => {
          ws.send(JSON.stringify({ session_id: sessionId, message: text }))
        }
      }

      watch(() => store.currentSessionId, () => scrollBottom())

      return {
        store, draft, chatList, filteredSessions, modeOptions, filterOptions,
        currentRole, currentSession, newSession, removeSession, clearSession, formatTime,
        renderMarkdown, onModeChange, send, attachments, onFiles, fileInput,
      }
    },
  }

  window.ChatPage = ChatPage
})()
