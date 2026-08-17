(function () {
  async function getJson(url) {
    const resp = await fetch(url)
    if (!resp.ok) {
      const text = await resp.text().catch(() => '')
      throw new Error(text || `请求失败：${resp.status}`)
    }
    return resp.json()
  }

  async function sendJson(url, method, body) {
    const resp = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!resp.ok) {
      const text = await resp.text().catch(() => '')
      let detail = text
      try { detail = JSON.parse(text).detail || text } catch (e) { /* 忽略 */ }
      throw new Error(detail)
    }
    return resp.json()
  }

  window.api = {
    get: getJson,
    post: (url, body) => sendJson(url, 'POST', body),
    put: (url, body) => sendJson(url, 'PUT', body),
    del: (url) => sendJson(url, 'DELETE'),
    upload: async (url, file) => {
      const form = new FormData()
      form.append('file', file)
      const resp = await fetch(url, { method: 'POST', body: form })
      if (!resp.ok) throw new Error((await resp.text().catch(() => '')) || '上传失败')
      return resp.json()
    },
    download: async (url, filename) => {
      const resp = await fetch(url)
      if (!resp.ok) throw new Error('下载失败')
      const blob = await resp.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(a.href), 1000)
    },
  }
})()
