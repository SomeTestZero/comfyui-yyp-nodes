// 本地前端补丁（仅非 comfy.org 部署生效）：让"运行"按钮不受 cloud 鉴权拖累。
//
// 前端把"提交任务"和 ComfyUI 账号的工作区鉴权绑在同一条路径上，且这些 await 都没有超时：
// ComfyApp.queuePrompt 里 `await waitForWorkspaceSwitch()` / `await getWorkspaceAuthToken()`
// 位于 try/finally 之外 —— cloud 不可达时要么弹"提示执行失败 / 用户未认证"并清空队列，
// 要么永久挂住把 app.processingQueue 留在 true，之后每次点运行都被开头那句
// `if (this.processingQueue) return false` 静默吃掉（只能 F5）。本地服务端并不需要 cloud token。
//
// 三处拦截：
//   1. cloud/Firebase 请求加超时，任何 cloud 调用不再无限等待（本地请求不碰）；
//   2. 工作区 token 拿不到时用占位值放行，本地排队照常提交；
//   3. 看门狗：单次提交长时间不返回时复位 app.processingQueue，按钮不会变哑巴。
// 状态：window.__yypLocalQueueFix（stubs = 占位放行次数，watchdogHits = 看门狗触发次数）。

const CLOUD_HOST_RE = /(^|\.)(comfy\.org|googleapis\.com|firebaseapp\.com|googleusercontent\.com)$/i
const CLOUD_FETCH_TIMEOUT_MS = 10_000 // 单次 cloud / Firebase 请求上限
const TOKEN_WAIT_MS = 2_500 // 等真 token 的时长，超时改用占位值
const SWITCH_WAIT_MS = 2_500 // 等工作区切换的时长
const STUCK_RESET_MS = 30_000 // 提交卡死多久后复位按钮状态
const LOCAL_TOKEN = 'local-offline' // 占位 token：本地服务端不校验

const state = (window.__yypLocalQueueFix = window.__yypLocalQueueFix || {
  installed: false,
  storesPatched: false,
  stubs: 0,
  watchdogHits: 0,
})

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function isCloudRequest(input) {
  const raw = typeof input === 'string' ? input : input?.url
  if (!raw) return false
  try {
    return CLOUD_HOST_RE.test(new URL(raw, location.href).hostname)
  } catch {
    return false
  }
}

function timeoutSignal(ms) {
  if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
    return { signal: AbortSignal.timeout(ms) }
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ms)
  return { signal: controller.signal, release: () => clearTimeout(timer) }
}

function patchCloudFetch() {
  if (state.fetchPatched) return
  state.fetchPatched = true
  const nativeFetch = window.fetch.bind(window)
  window.fetch = function (input, init) {
    if (!isCloudRequest(input) || init?.signal) return nativeFetch(input, init)
    const bound = timeoutSignal(CLOUD_FETCH_TIMEOUT_MS)
    const call = nativeFetch(input, { ...init, signal: bound.signal })
    return bound.release ? call.finally(bound.release) : call
  }
}

function findPinia() {
  const viaManager = window.app?.extensionManager?._p
  if (viaManager?._s) return viaManager
  const viaApp = document.getElementById('vue-app')?.__vue_app__?.config?.globalProperties?.$pinia
  if (viaApp?._s) return viaApp
  for (const el of document.querySelectorAll('div[id]')) {
    const pinia = el.__vue_app__?.config?.globalProperties?.$pinia
    if (pinia?._s) return pinia
  }
  return null
}

let pinia = null

function store(name) {
  pinia = pinia || findPinia()
  return pinia?._s?.get(name) ?? null
}

function patchStores() {
  if (state.storesPatched) return true
  const team = store('teamWorkspace')
  const auth = store('auth')
  const workspaceAuth = store('workspaceAuth')
  if (typeof auth?.getWorkspaceAuthToken !== 'function') return false
  if (typeof team?.waitForWorkspaceSwitch !== 'function') return false
  if (typeof workspaceAuth?.getWorkspaceToken !== 'function') return false

  // 工作区 token：先吃缓存；拿不到就不再等 cloud，给占位值让本地排队继续
  const realToken = auth.getWorkspaceAuthToken.bind(auth)
  auth.getWorkspaceAuthToken = async () => {
    let cached
    try {
      cached = workspaceAuth.getWorkspaceToken()
    } catch {
      cached = undefined
    }
    if (cached) return cached
    let token
    try {
      token = await Promise.race([realToken(), wait(TOKEN_WAIT_MS)])
    } catch {
      token = undefined
    }
    if (token || !team.activeWorkspaceId) return token
    state.stubs += 1
    return LOCAL_TOKEN
  }

  // 工作区切换：本地部署不该因为切换失败/超时拖住排队
  const realWait = team.waitForWorkspaceSwitch.bind(team)
  team.waitForWorkspaceSwitch = async () => {
    try {
      await Promise.race([realWait(), wait(SWITCH_WAIT_MS)])
    } catch {
      // 忽略：本地提交不依赖工作区切换结果
    }
  }

  state.storesPatched = true
  return true
}

function install(app) {
  if (app.__yypQueueFix) return true
  const original = app.queuePrompt
  if (typeof original !== 'function') return false
  app.__yypQueueFix = true
  app.queuePrompt = async function (...args) {
    patchStores()
    let timer
    if (!app.processingQueue) {
      // 这次调用才真正开始提交：卡住时复位按钮状态并丢掉堆积的点击，不必 F5
      timer = setTimeout(() => {
        if (!app.processingQueue) return
        app.processingQueue = false
        if (Array.isArray(app.queueItems)) app.queueItems.length = 0
        state.watchdogHits += 1
      }, STUCK_RESET_MS)
    }
    try {
      return await original.apply(app, args)
    } finally {
      clearTimeout(timer)
    }
  }
  return true
}

function bootstrap() {
  const app = window.app
  if (!app || typeof app.queuePrompt !== 'function') return false
  patchStores()
  if (!install(app)) return false
  if (!state.installed) {
    state.installed = true
    console.info('[yyp-local-queue-fix] active: cloud fetch timeout + local token fallback + queue watchdog')
  }
  return true
}

if (!CLOUD_HOST_RE.test(location.hostname)) {
  patchCloudFetch()
  if (!bootstrap()) {
    const poll = setInterval(() => {
      if (bootstrap()) clearInterval(poll)
    }, 300)
    setTimeout(() => clearInterval(poll), 60_000)
  }
}
