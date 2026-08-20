(function () {
  const { reactive } = Vue

  const store = reactive({
    page: 'chat',
    theme: 'dark',
    leftOpen: true,
    rightOpen: true,
    centered: true,
    roles: [],
    skills: [],
    modes: [],
    models: [],
    thinkingPresets: [],
    variables: [],
    variableGroups: {},
    sessions: [],
    sessionCharacters: [],
    currentSessionId: null,
    filterCharacter: '全部',
    messages: [],
    player: { name: '', avatar: '' },
    multimodal: { enabled: false, model: '', prompt: '' },
    autoTitle: { enabled: false, model: '', prompt: '', mode: 1, rounds: 3 },
    searchToolPrompt: '',
    streaming: false,
    ws: null,
    activeWs: null,
    activeAiMsg: null,
    streamingMsgs: {},
    branchTree: [],
    branchChoices: {},
    toast: { text: '', kind: '' },
  })

  function notify(text, kind = 'error') {
    store.toast = { text, kind }
    setTimeout(() => { store.toast = { text: '', kind: '' } }, 4000)
  }

  function normalizeMessages(msgs) {
    return msgs.map((m, i) => ({
      ...m,
      key: 'h' + (m.id || 'r' + i),
      content: m.content || '',
      reasoning: m.reasoning || '',
      blocks: Array.isArray(m.blocks) ? m.blocks : [],
      tool_events: Array.isArray(m.tool_events) ? m.tool_events : [],
      attachments: Array.isArray(m.attachments) ? m.attachments : [],
      interrupted: !!m.interrupted,
    }))
  }

  async function loadState() {
    const data = await api.get('/api/state')
    store.roles = data.roles
    store.skills = data.skills
    store.modes = data.modes
    store.models = data.models
    store.thinkingPresets = data.thinking_presets || []
    const v = await api.get('/api/variables')
    store.variables = v.variables || []
    store.variableGroups = v.groups || {}
    store.sessions = data.sessions
    store.sessionCharacters = data.session_characters
    store.player = data.player
    store.multimodal = data.multimodal || { enabled: false, model: '', prompt: '' }
    store.autoTitle = data.auto_title || { enabled: false, model: '', prompt: '', mode: 1, rounds: 3 }
    store.searchToolPrompt = data.search_tool_prompt || ''
    store.theme = data.ui.theme
    store.leftOpen = data.ui.sidebar_left
    store.rightOpen = data.ui.sidebar_right
    store.centered = data.ui.centered !== false
    applyTheme()
  }

  async function loadSessions() {
    store.sessions = await api.get('/api/sessions')
    const roles = [...new Set(store.sessions.map((s) => s.character_name).filter(Boolean))]
    store.sessionCharacters = roles
  }

  function currentSession() {
    return store.sessions.find((s) => s.id === store.currentSessionId) || null
  }

  function workdirName(path) {
    if (!path) return ''
    const norm = String(path).replace(/[\\/]+$/, '')
    const parts = norm.split(/[\\/]/)
    const name = parts[parts.length - 1] || ''
    return name || norm
  }

  function workdirSummary() {
    const s = currentSession()
    const list = (s && s.workdirs) || []
    if (!list.length) return '无工作目录'
    if (list.length === 1) return workdirName(list[0])
    return `${workdirName(list[0])} 等${list.length}个目录`
  }

  async function saveSessionWorkdirs(list) {
    const s = currentSession()
    if (!s) return
    const updated = await api.put(`/api/sessions/${s.id}`, { workdirs: list })
    const idx = store.sessions.findIndex((x) => x.id === s.id)
    if (idx >= 0) store.sessions[idx] = updated
    return updated
  }

  function currentRole() {
    const s = currentSession()
    if (!s) return null
    return store.roles.find((r) => r.name === s.character_name) || null
  }

  function computeVisibleMessages() {
    const msgs = store.messages
    const choices = store.branchChoices || {}
    const roots = [...new Set(msgs.map((m) => m.branch_root || 0).filter((r) => r))].sort((a, b) => a - b)
    const picks = {}
    const maxBranch = {}
    for (const r of roots) {
      const bs = msgs.filter((m) => (m.branch_root || 0) === r).map((m) => m.branch_id).sort((a, b) => a - b)
      maxBranch[r] = bs.length ? bs[bs.length - 1] : 0
      const pick = choices[r]
      picks[r] = bs.includes(pick) ? pick : maxBranch[r]
    }
    let cutRoot = null
    for (const r of roots) {
      if (picks[r] !== maxBranch[r]) { cutRoot = r; break }
    }
    if (cutRoot == null) {
      return msgs.filter((m) => {
        const root = m.branch_root || 0
        if (!root) return true
        return picks[root] === m.branch_id
      })
    }
    let cutTime = null
    for (const m of msgs) {
      if ((m.branch_root || 0) === cutRoot) {
        const ct = m.created_at || 0
        if (cutTime == null || ct < cutTime) cutTime = ct
      }
    }
    if (cutTime == null) cutTime = 0
    return msgs.filter((m) => {
      const root = m.branch_root || 0
      if (root === cutRoot) return m.branch_id === picks[cutRoot]
      if ((m.created_at || 0) > cutTime) return false
      if (!root) return true
      return picks[root] === m.branch_id
    })
  }

  async function selectSession(id) {
    store.currentSessionId = id
    const s = currentSession()
    if (!s) { store.messages = []; store.branchTree = []; store.branchChoices = {}; return }
    const msgs = await api.get(`/api/sessions/${id}/messages`)
    const list = normalizeMessages(msgs)
    const pending = store.streamingMsgs[id]
    if (pending && pending.streaming) {
      if (pending.id) {
        const idx = list.findIndex((m) => m.id === pending.id)
        if (idx >= 0) list[idx] = pending
        else list.push(pending)
      } else {
        list.push(pending)
      }
    }
    store.messages = list
    await loadBranches()
  }

  function branchNodeFor(id) {
    return store.branchTree.find((n) => n.message.id === id) || null
  }

  function branchIdsOf(node) {
    if (!node || !node.branches) return []
    return node.branches.map((b) => b.branch_id).sort((a, b) => a - b)
  }

  function branchOf(msg) {
    if (!msg || msg.sender !== 'player' || !msg.id) return null
    const node = branchNodeFor(msg.id)
    const ids = branchIdsOf(node)
    if (ids.length < 2) return null
    const cur = store.branchChoices[msg.id] !== undefined ? store.branchChoices[msg.id] : ids[ids.length - 1]
    const idx = ids.indexOf(cur)
    return { index: idx + 1, total: ids.length }
  }

  function lastNonLatestRoot() {
    let last = null
    for (const node of store.branchTree) {
      const rid = node.message.id
      if (rid == null) continue
      const ids = branchIdsOf(node)
      if (ids.length < 2) continue
      const cur = store.branchChoices[rid]
      if (cur !== undefined && cur !== ids[ids.length - 1]) last = rid
    }
    return last
  }

  async function loadBranches() {
    const sid = store.currentSessionId
    if (!sid) return
    const tree = await api.get(`/api/sessions/${sid}/branch-tree`)
    store.branchTree = tree
    const choices = { ...store.branchChoices }
    for (const node of tree) {
      const rid = node.message.id
      if (rid == null) continue
      const ids = branchIdsOf(node)
      if (!ids.length) continue
      const cur = choices[rid]
      if (cur === undefined || !ids.includes(cur)) choices[rid] = ids[ids.length - 1]
    }
    store.branchChoices = choices
  }

  async function refreshBranches(activeRoot = null) {
    const sid = store.currentSessionId
    if (!sid) return
    const tree = await api.get(`/api/sessions/${sid}/branch-tree`)
    store.branchTree = tree
    const choices = { ...store.branchChoices }
    for (const node of tree) {
      const rid = node.message.id
      if (rid == null) continue
      const ids = branchIdsOf(node)
      if (!ids.length) continue
      const cur = choices[rid]
      if (cur === undefined || !ids.includes(cur)) choices[rid] = ids[ids.length - 1]
      if (rid === activeRoot) choices[rid] = ids[ids.length - 1]
    }
    store.branchChoices = choices
  }

  async function applyBranch(msg, dir) {
    if (store.streaming) {
      notify('正在回复中，无法切换分支')
      return
    }
    if (!msg || !msg.id) {
      notify('切换分支失败：消息无效')
      return
    }
    const node = branchNodeFor(msg.id)
    const ids = branchIdsOf(node)
    if (!ids.length || ids.length < 2) {
      notify('切换分支失败：该消息没有可切换的分支')
      return
    }
    const cur = store.branchChoices[msg.id] !== undefined ? store.branchChoices[msg.id] : ids[ids.length - 1]
    const idx = ids.indexOf(cur)
    if (idx < 0) {
      notify('切换分支失败：当前分支状态异常')
      return
    }
    const next = ids[(idx + dir + ids.length) % ids.length]
    if (next === cur) return
    store.branchChoices = { ...store.branchChoices, [msg.id]: next }
    const sid = store.currentSessionId
    if (!sid) return
    try {
      const msgs = await api.get(`/api/sessions/${sid}/messages`)
      store.messages = store.normalizeMessages(msgs)
    } catch (e) {
      notify('刷新失败：' + (e.message || e))
    }
  }

  async function newSession() {
    const role = store.roles[0]
    if (!role) { notify('请先在角色页创建一个角色'); return }
    const session = await api.post('/api/sessions', { character_name: role.name })
    await loadState()
    await selectSession(session.id)
  }

  async function switchSessionRole(roleName) {
    const s = currentSession()
    if (!s) return
    const updated = await api.put(`/api/sessions/${s.id}`, { character_name: roleName })
    const idx = store.sessions.findIndex((x) => x.id === s.id)
    store.sessions[idx] = updated
  }

  async function switchSessionMode(modeName) {
    const s = currentSession()
    if (!s) return
    const updated = await api.put(`/api/sessions/${s.id}`, { mode: modeName || '' })
    const idx = store.sessions.findIndex((x) => x.id === s.id)
    store.sessions[idx] = updated
  }

  function applyTheme() {
    document.documentElement.setAttribute('data-theme', store.theme)
  }

  async function saveUi() {
    await api.put('/api/config', {
      ui: { theme: store.theme, sidebar_left: store.leftOpen, sidebar_right: store.rightOpen, centered: store.centered },
    })
  }

  store.notify = notify
  store.loadState = loadState
  store.loadSessions = loadSessions
  store.currentSession = currentSession
  store.currentRole = currentRole
  store.workdirName = workdirName
  store.workdirSummary = workdirSummary
  store.saveSessionWorkdirs = saveSessionWorkdirs
  store.selectSession = selectSession
  store.newSession = newSession
  store.switchSessionRole = switchSessionRole
  store.switchSessionMode = switchSessionMode
  store.applyTheme = applyTheme
  store.saveUi = saveUi
  store.normalizeMessages = normalizeMessages
  store.branchNodeFor = branchNodeFor
  store.branchOf = branchOf
  store.lastNonLatestRoot = lastNonLatestRoot
  store.loadBranches = loadBranches
  store.refreshBranches = refreshBranches
  store.applyBranch = applyBranch
  store.computeVisibleMessages = computeVisibleMessages

  window.store = store
})()
