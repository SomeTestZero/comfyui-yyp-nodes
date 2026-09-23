// 离线验证：模拟浏览器环境跑 web/local_queue_fix.js（不依赖 ComfyUI、不联网）
// 用法：node tests/test_local_queue_fix.mjs
import { pathToFileURL } from 'node:url'

const PATCH = new URL('../web/local_queue_fix.js', import.meta.url)
const realSetTimeout = globalThis.setTimeout
// 把长延时压缩，便于快速验证（STUCK 判定用 Date.now，用例里手动挪 lastSubmitAt 模拟时间流逝）
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, ms >= 1000 ? Math.round(ms / 50) : ms, ...rest)
const tick = (ms) => new Promise((r) => realSetTimeout(r, ms))

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
let tokenCalls = 0
let queueMode = 'ok'
const app = {
  processingQueue: false,
  queueItems: [],
  async queuePrompt(...args) {
    lastArgs = args
    app.queueItems.push('item') // 与真实 ComfyApp.queuePrompt 一致：先 push，while 循环里 pop
    if (app.processingQueue) return false
    app.processingQueue = true
    if (queueMode === 'ok') {
      app.queueItems.pop() // 消费一项后正常退出
      app.processingQueue = false
      return true
    }
    if (queueMode === 'fail') {
      app.processingQueue = false
      throw new Error('boom')
    }
    return new Promise(() => {}) // 永不 settle，模拟卡死
  },
}
let lastArgs
const stores = new Map()
const team = {
  activeWorkspaceId: 'ws-1',
  waitForWorkspaceSwitch: () => new Promise(() => {}), // 永不 settle
}
const auth = {
  async getWorkspaceAuthToken() {
    tokenCalls += 1
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

// 1. fetch：cloud 与本地 POST /prompt 加超时，其余本地请求不动，调用方自带 signal 不覆盖
await window.fetch('https://cloud.comfy.org/api/auth/token')
await window.fetch('/prompt', { method: 'POST' })
await window.fetch('/queue')
await window.fetch('/history', { method: 'POST' })
await window.fetch('https://cloud.comfy.org/x', { signal: 'mine' })
check('fetch cloud gets signal', fetchCalls[0]?.hasSignal === true, JSON.stringify(fetchCalls[0]))
check('fetch local POST /prompt gets signal', fetchCalls[1]?.hasSignal === true, JSON.stringify(fetchCalls[1]))
check('fetch local GET untouched', fetchCalls[2]?.hasSignal === false, JSON.stringify(fetchCalls[2]))
check('fetch local POST non-prompt untouched', fetchCalls[3]?.hasSignal === false, JSON.stringify(fetchCalls[3]))
check('fetch keeps caller signal', fetchCalls[4]?.hasSignal === true)

// 2. token：真 token 挂住 → 占位放行
const started = Date.now()
check('token fallback', (await auth.getWorkspaceAuthToken()) === 'local-offline')
check('token fallback waited ~2.5s(scaled)', Date.now() - started < 1000, `${Date.now() - started}ms`)
check('stubs counted', state.stubs === 1, String(state.stubs))
check('tokenCalls once', tokenCalls === 1, String(tokenCalls))

// 3. token：TTL 内再次拿不到 → 直接占位，不再等真 token
tokenCalls = 0
check('token stub cached', (await auth.getWorkspaceAuthToken()) === 'local-offline')
check('token stub skips real request', tokenCalls === 0, String(tokenCalls))
check('stubs unchanged', state.stubs === 1, String(state.stubs))
state.tokenStubUntil = 0 // 解除缓存，测后面的分支

// 4. token：cloud 抛错 → 同样占位放行（并写入 TTL 缓存）
tokenMode = 'throw'
check('token fallback on error', (await auth.getWorkspaceAuthToken()) === 'local-offline')
check('stubs counted(2)', state.stubs === 2, String(state.stubs))
state.tokenStubUntil = 0 // 解除缓存，让下一例走到真 token

// 5. token：真 token 很快就绪 → 用真 token，不进占位分支
tokenMode = 'fast'
check('real token used', (await auth.getWorkspaceAuthToken()) === 'real-token')
check('stubs unchanged', state.stubs === 2, String(state.stubs))

// 6. token：缓存命中 → 立即返回
cachedToken = 'cached-token'
check('cached token used', (await auth.getWorkspaceAuthToken()) === 'cached-token')

// 7. token：无活动工作区 → 保持 undefined（不塞占位值）
cachedToken = undefined
team.activeWorkspaceId = null
tokenMode = 'hang'
check('no workspace -> undefined', (await auth.getWorkspaceAuthToken()) === undefined)
check('stubs still 2', state.stubs === 2, String(state.stubs))
team.activeWorkspaceId = 'ws-1'

// 8. 工作区切换等待被限时
const t0 = Date.now()
await team.waitForWorkspaceSwitch()
check('workspace switch bounded', Date.now() - t0 < 1000, `${Date.now() - t0}ms`)

// 9. 正常提交：返回值透传、参数透传、标志位不残留
queueMode = 'ok'
check('normal submit passes through', (await app.queuePrompt(0, 1, {})) === true)
check('args pass through', lastArgs.length === 3 && lastArgs[0] === 0)
check('flag clean after normal submit', app.processingQueue === false)

// 10. 挂死后再次点击 → 立即复位接管（不需要等看门狗、不需要 F5）
queueMode = 'hang'
app.queueItems.length = 0
app.queuePrompt() // 不 await：发起一次挂死提交，processingQueue 卡 true
await tick(50)
check('stuck submit holds flag', app.processingQueue === true)
state.lastSubmitAt = Date.now() - 21_000 // 模拟 21s 过去
queueMode = 'ok'
check('click takeover resets stuck submit', (await app.queuePrompt(0, 1)) === true)
check('takeover leaves flag clean', app.processingQueue === false)
check('takeover cleared stale items', app.queueItems.length === 0, String(app.queueItems.length))
check('reset counted', state.watchdogHits === 1, String(state.watchdogHits))

// 11. 单飞窗口内的重复点击：维持上游语义（push 排队、返回 false、不误复位）
queueMode = 'hang'
app.queuePrompt() // 发起者，挂住
await tick(50)
check('in-flight holds flag', app.processingQueue === true)
const hitsBefore = state.watchdogHits
await tick(600) // 等看门狗到点：提交刚发起（真实时钟 <20s），不应被误杀
check('watchdog spares fresh submit', app.processingQueue === true)
check('watchdog not counted', state.watchdogHits === hitsBefore)

// 12. 看门狗：提交真挂死（把发起时间挪到 21s 前）→ 自动复位 + 清堆积项
app.processingQueue = false // 上一例的挂死提交由手动复位收尾
app.queueItems.length = 0
queueMode = 'hang'
app.queuePrompt() // 重新发起挂死提交，设下看门狗
await tick(50)
state.lastSubmitAt = Date.now() - 21_000 // 在看门狗触发前伪造时间流逝
app.queueItems.push('stale-1', 'stale-2')
await tick(600)
check('watchdog auto reset', app.processingQueue === false)
check('watchdog cleared items', app.queueItems.length === 0, String(app.queueItems.length))
check('watchdog counted', state.watchdogHits === hitsBefore + 1, `${state.watchdogHits} vs ${hitsBefore}`)

// 13. 提交抛错不残留标志位
queueMode = 'fail'
let caught = false
try {
  await app.queuePrompt(0, 1)
} catch {
  caught = true
}
check('error propagates', caught)
check('flag clean after error', app.processingQueue === false)

// 14. cloud 部署跳过
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
