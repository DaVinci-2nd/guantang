(function () {
  const { reactive, ref, computed, watch, nextTick } = Vue

  let keySeq = 0
  let attSeq = 0

  const ChatPage = {
    template: document.getElementById('tpl-chat-page').innerHTML,
    setup() {
      const chatList = ref(null)
      const editorEl = ref(null)
      const fileInput = ref(null)
      const editTarget = ref(null)
      const editText = ref('')
      const editBlocks = ref(null)
      const debugData = ref(null)
      const previewTarget = ref(null)
      const editorDirty = ref(false)
      const editingAtts = ref([])
      const superData = ref(null)

      let activeWs = null
      let activeAiMsg = null

      const filteredSessions = computed(() => {
        if (store.filterCharacter === '全部') return store.sessions
        return store.sessions.filter((s) => s.character_name === store.filterCharacter)
      })

      const modeOptions = computed(() => {
        const role = store.currentRole()
        return role && role.modes ? role.modes : []
      })

      const filterOptions = computed(() => ['全部', ...store.sessionCharacters])

      const canSend = computed(() => editorDirty.value || editingAtts.value.length > 0)

      function nearBottom() {
        const el = chatList.value
        if (!el) return true
        return el.scrollHeight - el.scrollTop - el.clientHeight < 80
      }

      function scrollBottom(force = false) {
        if (!force && !nearBottom()) return
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

      function svgHtml(name, size) {
        const s = size || 14
        return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${window.ICONS[name] || ''}</svg>`
      }

      function isImageFile(name) {
        return /\.(png|jpe?g|gif|webp|bmp|heic|heif)$/i.test(name)
      }

      function makeThumb(file) {
        return new Promise((resolve) => {
          const img = new Image()
          const url = URL.createObjectURL(file)
          img.onload = () => {
            const max = 160
            let w = img.width
            let h = img.height
            if (w > max || h > max) {
              const ratio = Math.min(max / w, max / h)
              w = Math.round(w * ratio)
              h = Math.round(h * ratio)
            }
            const canvas = document.createElement('canvas')
            canvas.width = w
            canvas.height = h
            const ctx = canvas.getContext('2d')
            ctx.drawImage(img, 0, 0, w, h)
            URL.revokeObjectURL(url)
            resolve(canvas.toDataURL('image/png'))
          }
          img.onerror = () => {
            URL.revokeObjectURL(url)
            resolve('')
          }
          img.src = url
        })
      }

      function markDirty() {
        editorDirty.value = true
      }

      function insertAttachTag(item) {
        const el = editorEl.value
        if (!el) return
        el.focus()
        const span = document.createElement('span')
        span.className = 'attach-inline'
        span.contentEditable = 'false'
        span.dataset.aid = String(item.id)
        if (item.kind === 'image' && item.thumb) {
          const img = document.createElement('img')
          img.className = 'attach-thumb'
          img.src = item.thumb
          img.alt = item.name
          span.appendChild(img)
          const name = document.createElement('span')
          name.className = 'attach-inline-name'
          name.textContent = item.name
          span.appendChild(name)
        } else {
          const icon = document.createElement('span')
          icon.className = 'attach-inline-icon'
          icon.innerHTML = svgHtml(item.kind === 'image' ? 'image' : 'file_text')
          span.appendChild(icon)
          const name = document.createElement('span')
          name.className = 'attach-inline-name'
          name.textContent = item.name
          span.appendChild(name)
        }
        const del = document.createElement('button')
        del.className = 'icon-btn small attach-inline-del'
        del.title = '移除'
        del.innerHTML = svgHtml('x', 12)
        span.appendChild(del)
        span.addEventListener('click', (ev) => {
          if (ev.target.closest('.attach-inline-del')) return
          previewAttach(item)
        })
        del.addEventListener('click', (ev) => {
          ev.stopPropagation()
          span.remove()
          const idx = editingAtts.value.findIndex((a) => a.id === item.id)
          if (idx >= 0) editingAtts.value.splice(idx, 1)
          markDirty()
        })
        const sel = window.getSelection()
        const zwsp = document.createTextNode('\u200b')
        if (sel && sel.rangeCount) {
          const range = sel.getRangeAt(0)
          range.collapse(false)
          range.insertNode(span)
          range.insertNode(zwsp)
          const after = document.createRange()
          after.setStartAfter(zwsp)
          after.collapse(true)
          sel.removeAllRanges()
          sel.addRange(after)
        } else {
          el.appendChild(span)
          el.appendChild(zwsp)
        }
        markDirty()
      }

      async function onFiles(e) {
        const files = [...e.target.files]
        e.target.value = ''
        for (const f of files) {
          if (isImageFile(f.name)) {
            const item = {
              id: attSeq++, name: f.name, size: f.size, kind: 'image',
              readable: true, content: '', file: f, thumb: await makeThumb(f), url: '',
            }
            editingAtts.value.push(item)
            insertAttachTag(item)
          } else {
            let readable = false
            let content = ''
            if (f.size <= 2 * 1024 * 1024) {
              try {
                const buf = await f.arrayBuffer()
                new TextDecoder('utf-8', { fatal: true }).decode(buf)
                content = new TextDecoder('utf-8').decode(buf)
                readable = true
              } catch (err) {
                readable = false
              }
            }
            const item = {
              id: attSeq++, name: f.name, size: f.size, kind: readable ? 'text' : 'binary',
              readable, content, file: null, thumb: '', url: '',
            }
            editingAtts.value.push(item)
            insertAttachTag(item)
          }
        }
      }

      function previewAttach(item) {
        if (item.kind === 'text' && item.content) {
          previewTarget.value = { kind: 'text', name: item.name, content: item.content }
        } else if (item.kind === 'image') {
          const url = item.thumb || (item.file ? URL.createObjectURL(item.file) : '')
          if (url) previewTarget.value = { kind: 'image', name: item.name, url }
        }
      }

      function openPreview(payload) {
        if (payload.kind === 'image' && payload.url) {
          previewTarget.value = { kind: 'image', name: payload.name, url: payload.url }
        } else if (payload.kind === 'text' && payload.content) {
          previewTarget.value = { kind: 'text', name: payload.name, content: payload.content }
        }
      }

      function attachExt(name) {
        const m = /\.([a-zA-Z0-9]+)$/.exec(name || '')
        return m ? m[1].toLowerCase() : 'txt'
      }

      function extractEditorContent() {
        const el = editorEl.value
        let text = ''
        if (!el) return { text, atts: editingAtts.value }
        function walk(node) {
          node.childNodes.forEach((child) => {
            if (child.nodeType === Node.TEXT_NODE) {
              text += child.textContent.replace(/\u200b/g, '')
            } else if (child.nodeType === Node.ELEMENT_NODE) {
              if (child.classList && child.classList.contains('attach-inline')) {
                const item = editingAtts.value.find((a) => a.id === Number(child.dataset.aid))
                if (item) {
                  if (item.kind === 'text') {
                    const ext = attachExt(item.name)
                    text += `【附件：${item.name}】\n\`\`\`${ext}\n${item.content}\n\`\`\``
                  } else if (item.kind === 'image') {
                    text += `【图片：${item.name}】`
                  } else {
                    text += `【附件：${item.name}】（二进制文件，内容无法直接读取）`
                  }
                }
              } else {
                walk(child)
              }
            }
          })
        }
        walk(el)
        return { text: text.trimEnd(), atts: editingAtts.value }
      }

      function clearEditor() {
        if (editorEl.value) editorEl.value.innerHTML = ''
        editingAtts.value = []
        editorDirty.value = false
      }

      function onEditorKeydown(e) {
        if (e.key === 'Enter') {
          if (e.ctrlKey && e.shiftKey) {
            e.preventDefault()
            superSend()
            return
          }
          if (e.shiftKey) return
          e.preventDefault()
          send()
        }
      }

      function onEditorPaste(e) {
        e.preventDefault()
        const text = e.clipboardData.getData('text/plain')
        if (text) document.execCommand('insertText', false, text)
        markDirty()
      }

      async function send() {
        if (store.streaming) return
        const { text, atts } = extractEditorContent()
        if (!text.trim() && !atts.length) return
        const meta = []
        for (const a of atts) {
          if (a.kind === 'image') {
            if (!a.url) {
              try {
                const res = await api.upload('/api/files', a.file)
                a.url = res.url
              } catch (e) {
                store.notify('图片上传失败：' + e.message)
                return
              }
            }
            meta.push({ name: a.name, size: a.size, kind: 'image', readable: true, url: a.url })
          } else {
            meta.push({ name: a.name, size: a.size, kind: a.kind, readable: a.readable, content: a.readable ? a.content : '' })
          }
        }
        clearEditor()
        await sendContent(text, meta, store.currentSessionId)
      }

      async function superSend() {
        if (store.streaming) return
        const sid = store.currentSessionId
        if (!sid) return
        const { text, atts } = extractEditorContent()
        if (!text.trim() && !atts.length) return
        try {
          const ctxData = await api.get(`/api/sessions/${sid}/submit-context?message=${encodeURIComponent(text)}`)
          const rows = [{ role: 'system', content: ctxData.system || '' }]
          ;(ctxData.messages || []).forEach((m) => {
            rows.push({ role: m.role, content: m.content || '' })
          })
          superData.value = rows
        } catch (e) {
          store.notify(e.message)
        }
      }

      function addSuperRow() {
        if (superData.value) superData.value.push({ role: 'user', content: '' })
      }

      function removeSuperRow(index) {
        if (superData.value) superData.value.splice(index, 1)
      }

      function superRoleName(role) {
        if (role === 'system') return '系统提示词'
        if (role === 'user') return '玩家消息'
        return '角色消息'
      }

      async function submitSuper() {
        if (!superData.value || store.streaming) return
        const rows = superData.value.filter((r) => r.role && r.content !== undefined)
        const sid = store.currentSessionId
        if (!sid) return
        clearEditor()
        superData.value = null
        await sendContent('', [], sid, true, rows)
      }

      async function sendContent(content, attList, sessionId, persistPlayer = true, superMessages = null) {
        if (store.streaming) return
        const sid = sessionId || store.currentSessionId
        if (!sid) return
        const isSuper = !!superMessages
        store.streaming = true
        if (persistPlayer && !isSuper) {
          let saved
          try {
            saved = await api.post(`/api/sessions/${sid}/messages`, { content, attachments: attList || [] })
          } catch (e) {
            store.streaming = false
            store.notify(e.message)
            return
          }
          saved.key = 'p' + keySeq++
          store.messages.push(saved)
        } else if (isSuper) {
          const playerRow = [...superMessages].reverse().find((m) => m.role === 'user')
          const saved = {
            key: 'p' + keySeq++,
            sender: 'player',
            content: playerRow ? playerRow.content : content,
            attachments: [],
            created_at: Date.now() / 1000,
          }
          store.messages.push(saved)
        }

        const aiMsg = reactive({
          key: 'a' + keySeq++,
          sender: 'character',
          character_name: store.currentSession().character_name,
          content: '',
          reasoning: '',
          tool_events: [],
          blocks: [],
          streaming: true,
          interrupted: false,
          created_at: Date.now() / 1000,
        })
        store.messages.push(aiMsg)
        store.streamingMsgs[sid] = aiMsg
        activeAiMsg = aiMsg
        scrollBottom(true)

        const ws = new WebSocket(`ws://${location.host}/ws/chat`)
        activeWs = ws
        ws.onmessage = (ev) => {
          const data = JSON.parse(ev.data)
          if (data.type === 'reasoning') {
            aiMsg.reasoning += data.delta
            const last = aiMsg.blocks[aiMsg.blocks.length - 1]
            if (last && last.type === 'reasoning') last.text += data.delta
            else aiMsg.blocks.push({ type: 'reasoning', text: data.delta, open: false })
            scrollBottom()
          } else if (data.type === 'text') {
            aiMsg.content += data.delta
            const last = aiMsg.blocks[aiMsg.blocks.length - 1]
            if (last && last.type === 'text') last.text += data.delta
            else aiMsg.blocks.push({ type: 'text', text: data.delta })
            scrollBottom()
          } else if (data.type === 'recognized') {
            const m = data.message
            const idx = store.messages.findIndex((x) => x.id === m.id)
            if (idx >= 0) {
              store.messages[idx].content = m.content
              store.messages[idx].attachments = m.attachments
            }
          } else if (data.type === 'tool_call') {
            aiMsg.tool_events.push({ name: data.name, arguments: data.arguments, result: '' })
            aiMsg.blocks.push({ type: 'tool', name: data.name, arguments: data.arguments, status: 'running', result: '', open: false })
          } else if (data.type === 'tool_exec') {
            const te = [...aiMsg.tool_events].reverse().find((t) => t.name === data.name && (t.result === '' || t.result === '执行中…'))
            if (te) te.result = '执行中…'
            scrollBottom()
          } else if (data.type === 'approval') {
            const tb = [...aiMsg.blocks].reverse().find((b) => b.type === 'tool' && b.name === data.name && b.status === 'running')
            if (tb) {
              tb.status = 'pending'
              tb.approval_id = data.approval_id
              tb.approval_text = data.operation
              tb.approval_args = data.arguments
              tb.approval_diff = data.diff || null
            }
            scrollBottom()
            if (data.diff && data.diff.mode !== 'command' && data.diff.lines && data.diff.lines.length) {
              nextTick(() => {
                const box = document.querySelector('.approval-box')
                const scroll = box ? box.querySelector('.approval-diff-scroll') : null
                if (!scroll) return
                const first = scroll.querySelector('.diff-line.del, .diff-line.add')
                if (first) {
                  const r1 = first.getBoundingClientRect()
                  const r2 = scroll.getBoundingClientRect()
                  scroll.scrollTop = Math.max(0, scroll.scrollTop + r1.top - r2.top - 8)
                }
              })
            }
          } else if (data.type === 'tool_result') {
            const te = [...aiMsg.tool_events].reverse().find((t) => t.name === data.name && (t.result === '' || t.result === '执行中…'))
            if (te) te.result = data.text
            const tb = [...aiMsg.blocks].reverse().find((b) => b.type === 'tool' && b.name === data.name && (b.status === 'running' || b.status === 'pending'))
            if (tb) {
              tb.status = 'done'
              tb.result = data.text
            }
            scrollBottom()
          } else if (data.type === 'title') {
            const s = store.sessions.find((x) => x.id === data.session_id)
            if (s) {
              s.title = data.title
              s.title_set = 1
            }
          } else if (data.type === 'error') {
            aiMsg.content = aiMsg.content || ''
            store.notify(data.text)
            aiMsg.streaming = false
            store.streaming = false
            if (store.streamingMsgs[sid] === aiMsg) delete store.streamingMsgs[sid]
            ws.close()
          } else if (data.type === 'end') {
            aiMsg.streaming = false
            aiMsg.id = data.message.id
            aiMsg.created_at = data.message.created_at
            aiMsg.tool_events = data.message.tool_events
            aiMsg.reasoning = data.message.reasoning
            aiMsg.content = data.message.content
            aiMsg.interrupted = !!data.interrupted
            if (data.message.blocks && data.message.blocks.length) {
              aiMsg.blocks = data.message.blocks
            }
            store.streaming = false
            if (store.streamingMsgs[sid] === aiMsg) delete store.streamingMsgs[sid]
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
        ws.onclose = () => {
          aiMsg.blocks.forEach((b) => {
            if (b.type === 'tool' && b.status === 'pending') {
              b.status = 'done'
              b.result = '已中断'
            }
          })
          if (aiMsg.streaming) {
            aiMsg.streaming = false
            aiMsg.interrupted = true
            store.streaming = false
            if (store.streamingMsgs[sid] === aiMsg) delete store.streamingMsgs[sid]
          }
        }
        ws.onopen = () => {
          if (isSuper) {
            ws.send(JSON.stringify({ session_id: sid, super_messages: superMessages }))
          } else {
            ws.send(JSON.stringify({ session_id: sid, message: content }))
          }
        }
      }

      async function onSendClick(e) {
        if (e.shiftKey && e.ctrlKey) superSend()
        else send()
      }

      function abortStream() {
        if (!store.streaming || !activeWs) return
        const aiMsg = activeAiMsg
        activeWs.close()
        if (aiMsg) {
          aiMsg.streaming = false
          aiMsg.interrupted = true
        }
        store.streaming = false
      }

      function onApprove(block, decision) {
        const ws = activeWs
        if (!ws || ws.readyState !== WebSocket.OPEN) return
        if (block.approval_id) {
          ws.send(JSON.stringify({ type: 'approval_response', approval_id: block.approval_id, decision }))
        }
        block.status = 'running'
        block.approval_id = null
      }

      async function newSession() {
        await store.newSession()
        scrollBottom(true)
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

      async function deleteCurrentSession() {
        const sid = store.currentSessionId
        if (!sid) return
        if (!confirm('删除当前会话？')) return
        await api.del(`/api/sessions/${sid}`)
        store.currentSessionId = null
        store.messages = []
        await store.loadSessions()
      }

      function findPlayerMsg(msg) {
        if (msg.sender === 'player') return msg
        const idx = store.messages.findIndex((m) => m.key === msg.key)
        for (let i = idx - 1; i >= 0; i--) {
          if (store.messages[i].sender === 'player') return store.messages[i]
        }
        return null
      }

      async function regenerate(msg) {
        const player = findPlayerMsg(msg)
        if (!player || store.streaming) return
        const sid = store.currentSessionId
        if (!sid) return
        if (player.id) {
          await api.del(`/api/sessions/${sid}/messages?after=${player.id}`)
        }
        const idx = store.messages.findIndex((m) => m.key === player.key)
        store.messages = store.messages.slice(0, idx + 1)
        await sendContent(player.content, player.attachments || [], sid, false)
      }

      function openEdit(msg) {
        editTarget.value = msg
        editText.value = msg.content || ''
        if (msg.sender === 'character' && msg.blocks && msg.blocks.length) {
          editBlocks.value = msg.blocks.map((b) => {
            const c = { ...b }
            if (b.type === 'tool') c.argsText = JSON.stringify(b.arguments || {}, null, 2)
            return c
          })
        } else {
          editBlocks.value = null
        }
      }

      function closeEdit() {
        editTarget.value = null
        editBlocks.value = null
      }

      function blockTypeName(type) {
        if (type === 'reasoning') return '思考'
        if (type === 'tool') return '工具调用'
        return '正文'
      }

      function moveBlock(index, offset) {
        const target = index + offset
        if (target < 0 || target >= editBlocks.value.length) return
        const arr = editBlocks.value
        const tmp = arr[index]
        arr[index] = arr[target]
        arr[target] = tmp
      }

      async function saveEdit(sendAfter) {
        const msg = editTarget.value
        const sid = store.currentSessionId
        if (!msg || !sid) return
        const isPlayer = msg.sender === 'player'
        const body = { content: editText.value }
        if (isPlayer) {
          try {
            const updated = await api.put(`/api/sessions/${sid}/messages/${msg.id}`, body)
            msg.content = updated.content
          } catch (e) {
            store.notify(e.message)
            return
          }
          editTarget.value = null
          if (sendAfter) {
            const player = msg
            if (player.id) {
              await api.del(`/api/sessions/${sid}/messages?after=${player.id}`)
            }
            const idx = store.messages.findIndex((m) => m.key === player.key)
            store.messages = store.messages.slice(0, idx + 1)
            await sendContent(player.content, player.attachments || [], sid, false)
          }
          return
        }
        if (editBlocks.value && editBlocks.value.length) {
          const blocks = editBlocks.value.map((b) => {
            const c = { ...b }
            delete c.open
            if (c.type === 'tool') {
              try {
                c.arguments = JSON.parse(c.argsText || '{}')
              } catch (e) {
                c.arguments = c.arguments || {}
              }
              delete c.argsText
            }
            return c
          })
          body.blocks = blocks
          body.content = blocks.filter((b) => b.type === 'text').map((b) => b.text || '').join('')
        } else {
          body.blocks = []
        }
        try {
          const updated = await api.put(`/api/sessions/${sid}/messages/${msg.id}`, body)
          msg.content = updated.content
          msg.blocks = updated.blocks || []
        } catch (e) {
          store.notify(e.message)
          return
        }
        editTarget.value = null
        editBlocks.value = null
      }

      async function showDebug() {
        const sid = store.currentSessionId
        if (!sid) return
        try {
          debugData.value = await api.get(`/api/sessions/${sid}/debug`)
          if (debugData.value.send_log) debugData.value.send_log.forEach((s) => { s.open = true })
        } catch (e) {
          store.notify(e.message)
        }
      }

      function debugJson(value) {
        try { return JSON.stringify(value, null, 2) } catch (e) { return String(value) }
      }

      function formatTime(t) {
        if (!t) return ''
        const d = new Date(t * 1000)
        const pad = (n) => String(n).padStart(2, '0')
        return `${d.getMonth() + 1}-${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
      }

      function renderMarkdown(text) {
        return window.renderMarkdown(text)
      }

      async function onModeChange(e) {
        await store.switchSessionMode(e.target.value)
      }

      async function toggleCentered() {
        store.centered = !store.centered
        await store.saveUi()
      }

      watch(() => store.currentSessionId, () => scrollBottom(true))

      return {
        store, chatList, editorEl, fileInput, filteredSessions, modeOptions, filterOptions,
        currentRole, currentSession, newSession, deleteCurrentSession, formatTime,
        renderMarkdown, onModeChange, send, onFiles, toggleCentered, canSend,
        abortStream, regenerate, editTarget, editText, editBlocks, openEdit, closeEdit, saveEdit,
        blockTypeName, moveBlock, onApprove,
        debugData, showDebug, debugJson, previewTarget, openPreview,
        onEditorKeydown, onEditorPaste, onEditorInput: markDirty,
        superData, superSend, addSuperRow, removeSuperRow, superRoleName, submitSuper, onSendClick,
      }
    },
  }

  window.ChatPage = ChatPage
})()
