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
    streaming: false,
    ws: null,
    streamingMsgs: {},
    toast: { text: '', kind: '' },
  })

  function notify(text, kind = 'error') {
    store.toast = { text, kind }
    setTimeout(() => { store.toast = { text: '', kind: '' } }, 4000)
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

  function currentRole() {
    const s = currentSession()
    if (!s) return null
    return store.roles.find((r) => r.name === s.character_name) || null
  }

  async function selectSession(id) {
    store.currentSessionId = id
    const s = currentSession()
    if (!s) { store.messages = []; return }
    const msgs = await api.get(`/api/sessions/${id}/messages`)
    const list = msgs.map((m) => ({ ...m, key: 'h' + m.id }))
    const pending = store.streamingMsgs[id]
    if (pending) list.push(pending)
    store.messages = list
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
  store.selectSession = selectSession
  store.newSession = newSession
  store.switchSessionRole = switchSessionRole
  store.switchSessionMode = switchSessionMode
  store.applyTheme = applyTheme
  store.saveUi = saveUi

  window.store = store
})()
