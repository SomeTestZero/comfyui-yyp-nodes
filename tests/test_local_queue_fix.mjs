// 离线验证：模拟浏览器环境跑 web/local_queue_fix.js（不依赖 ComfyUI、不联网）
// 用法：node tests/test_local_queue_fix.mjs
import { pathToFileURL } from 'node:url'

const PATCH = new URL('../web/local_queue_fix.js', import.meta.url)
const realSetTimeout = globalThis.setTimeout
// 把长延时压缩，便于快速验证
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, ms >= 1000 ? Math.round(ms / 50) : ms, ...rest)

const results = []
const check = (name, cond, extra = '') => {
  results.push({ name, ok: !!cond, extra })
}

// --- 假浏览器环境 ---
globalThis.location = { href: 'http://127.0.0.1:8288/', hostname: '127.0.0.1' }
globalThis.document = { getElementById: () => null, querySelectorAll: () => [] }
const fetchCalls = []
globalThis.window = {
  fetch: async (input, init) => {
    fetchCalls.push({ input: String(input), hasSignal: init?.signal !== undefined })
    return { ok: true }
  },
}

let tokenMode = 'hang'
let queueMode = 'hang'
const app = {
  processingQueue: false,
  queueItems: [],
  async queuePrompt() {
    app.processingQueue = true // 与真实 ComfyApp.queuePrompt 一致
    if (queueMode === 'ok') {
      app.processingQueue = false
      return true
    }
    return new Promise(() => {}) // 永不 settle，模拟卡死
  },
}
const stores = new Map()
const team = {
  activeWorkspaceId: 'ws-1',
  waitForWorkspaceSwitch: () => new Promise(() => {}), // 永不 settle
}
const auth = {
  async getWorkspaceAuthToken() {
    if (tokenMode === 'fast') return 'real-token'
    if (tokenMode === 'throw') throw new Error('cloud down')
    return new Promise(() => {})
  },
}
let cachedToken
const workspaceAuth = { getWorkspaceToken: () => cachedToken }
stores.set('teamWorkspace', team)
stores.set('auth', auth)
stores.set('workspaceAuth', workspaceAuth)
app.extensionManager = { _p: { _s: stores } }
globalThis.window.app = app

// --- 载入补丁 ---
await import(pathToFileURL(PATCH.pathname.slice(1)).href + '?v=1')
const state = globalThis.window.__yypLocalQueueFix

check('installed', state.installed === true, JSON.stringify(state))
check('storesPatched', state.storesPatched === true)

// 1. fetch：cloud 加超时，本地不动，调用方自带 signal 不覆盖
await window.fetch('https://cloud.comfy.org/api/auth/token')
await window.fetch('/prompt')
await window.fetch('https://cloud.comfy.org/x', { signal: 'mine' })
check('fetch cloud gets signal', fetchCalls[0]?.hasSignal === true, JSON.stringify(fetchCalls[0]))
check('fetch local untouched', fetchCalls[1]?.hasSignal === false, JSON.stringify(fetchCalls[1]))
check('fetch keeps caller signal', fetchCalls[2]?.hasSignal === true)

// 2. token：真 token 挂住 → 占位放行
const started = Date.now()
check('token fallback', (await auth.getWorkspaceAuthToken()) === 'local-offline')
check('token fallback waited ~2.5s(scaled)', Date.now() - started < 1000, `${Date.now() - started}ms`)
check('stubs counted', state.stubs === 1, String(state.stubs))

// 3. token：cloud 抛错 → 同样占位放行
tokenMode = 'throw'
check('token fallback on error', (await auth.getWorkspaceAuthToken()) === 'local-offline')
check('stubs counted(2)', state.stubs === 2, String(state.stubs))

// 4. token：真 token 很快就绪 → 用真 token，不进占位分支
tokenMode = 'fast'
check('real token used', (await auth.getWorkspaceAuthToken()) === 'real-token')
check('stubs unchanged', state.stubs === 2, String(state.stubs))

// 5. token：缓存命中 → 立即返回
cachedToken = 'cached-token'
check('cached token used', (await auth.getWorkspaceAuthToken()) === 'cached-token')

// 6. token：无活动工作区 → 保持 undefined（不塞占位值）
cachedToken = undefined
team.activeWorkspaceId = null
tokenMode = 'hang'
check('no workspace -> undefined', (await auth.getWorkspaceAuthToken()) === undefined)
check('stubs still 2', state.stubs === 2, String(state.stubs))
team.activeWorkspaceId = 'ws-1'

// 7. 工作区切换等待被限时
const t0 = Date.now()
await team.waitForWorkspaceSwitch()
check('workspace switch bounded', Date.now() - t0 < 1000, `${Date.now() - t0}ms`)

// 8. 看门狗：提交卡死 → processingQueue 复位 + 清掉堆积点击
app.queueItems.push({ number: 1 }, { number: 2 })
app.queuePrompt() // 不 await：原函数永不 settle
await new Promise((r) => realSetTimeout(r, 1200))
check('watchdog cleared flag', app.processingQueue === false)
check('watchdog cleared queueItems', app.queueItems.length === 0, String(app.queueItems.length))
check('watchdogHits', state.watchdogHits === 1, String(state.watchdogHits))

// 9. 正常提交不受影响（返回值透传、标志位不残留）
queueMode = 'ok'
app.processingQueue = false
check('normal submit passes through', (await app.queuePrompt(0, 1, {})) === true)
check('flag clean after normal submit', app.processingQueue === false)

// 10. cloud 部署跳过
const before = window.fetch
globalThis.location = { href: 'https://cloud.comfy.org/', hostname: 'cloud.comfy.org' }
await import(pathToFileURL(PATCH.pathname.slice(1)).href + '?v=2')
check('cloud host skipped', window.fetch === before)

let failed = 0
for (const r of results) {
  if (!r.ok) failed += 1
  console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.extra ? '   [' + r.extra + ']' : ''}`)
}
console.log(failed === 0 ? `\nALL ${results.length} CHECKS PASSED` : `\n${failed} CHECK(S) FAILED`)
process.exit(failed === 0 ? 0 : 1)
