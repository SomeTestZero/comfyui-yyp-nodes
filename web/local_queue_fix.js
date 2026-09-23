// 本地前端补丁（仅非 comfy.org 部署生效）：让"运行"按钮不被挂死的提交卡成哑巴。
//
// ComfyApp.queuePrompt 的提交链上有多个无超时的 await（1.51.10 源码核实）：
//   - await teamWorkspaceStore.waitForWorkspaceSwitch()  在 try/finally 之外；
//   - await useAuthStore().getWorkspaceAuthToken()       在 try/finally 之外，且上游连 catch 都没有；
//   - 本地 POST /prompt 的 fetch 没有超时，服务器忙碌时挂几十秒。
// 任何一个挂住，app.processingQueue 就停在 true，之后每次点运行都被入口的
// `if (this.processingQueue) return false` 静默吃掉（点了没反应），只能 F5。
//
// 四层拦截：
//   1. cloud / Firebase 请求 10s 超时；本地 POST /prompt 45s 兜底超时（超时后走
//      上游 catch 弹"提示执行失败"并复位状态，而不是永远挂住）；
//   2. 工作区 token 拿不到就占位放行，本地排队照常提交；占位结果缓存 10 分钟，
//      期间点击不再白等 2.5s；
//   3. queuePrompt wrap：一次提交发起 20s 仍未结束视为挂死；之后任何一次点击
//      立即复位状态机并接管本次提交，无需 F5；
//   4. 兜底看门狗：挂死 25s 后自动复位，即使不再点击。
// 三个 wrap 都按"方法身份"幂等，pinia store 重建后重新包上。
// 诊断：window.__yypLocalQueueFix（stubs = 占位放行次数，watchdogHits = 自动复位次数）。

const CLOUD_HOST_RE = /(^|\.)(comfy\.org|googleapis\.com|firebaseapp\.com|googleusercontent\.com)$/i
const CLOUD_FETCH_TIMEOUT_MS = 10_000 // 单次 cloud / Firebase 请求上限
const PROMPT_TIMEOUT_MS = 45_000 // 本地 POST /prompt 兜底上限，正常 <2s
const TOKEN_WAIT_MS = 2_500 // 等真 token 的时长，超时改用占位值
const SWITCH_WAIT_MS = 2_500 // 等工作区切换的时长
const TOKEN_STUB_TTL_MS = 10 * 60_000 // 占位 token 缓存时长
const STUCK_RESET_MS = 20_000 // 提交多久没结束视为挂死
const WATCHDOG_MS = STUCK_RESET_MS + 5_000 // 看门狗轮询点（触发时按 STUCK_RESET_MS 判定）
const LOCAL_TOKEN = 'local-offline' // 占位 token：本地服务端不校验
const LOCAL_ORIGIN = new URL(location.href).origin

const state = (window.__yypLocalQueueFix = window.__yypLocalQueueFix || {
  installed: false,
  storesPatched: false,
  stubs: 0,
  watchdogHits: 0,
  lastSubmitAt: 0,
  tokenStubUntil: 0,
})

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

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
    if (init?.signal) return nativeFetch(input, init)
    let url = null
    try {
      url = new URL(typeof input === 'string' ? input : input?.url, location.href)
    } catch {
      return nativeFetch(input, init)
    }
    const isCloud = CLOUD_HOST_RE.test(url.hostname)
    const isPrompt =
      url.origin === LOCAL_ORIGIN &&
      url.pathname === '/prompt' &&
      (init?.method || 'GET').toUpperCase() === 'POST'
    if (!isCloud && !isPrompt) return nativeFetch(input, init)
    const bound = timeoutSignal(isCloud ? CLOUD_FETCH_TIMEOUT_MS : PROMPT_TIMEOUT_MS)
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

// 工作区 token：先吃缓存；拿不到就限时等 cloud，再不行给占位值让本地排队继续。
// 占位结果缓存 TOKEN_STUB_TTL_MS，避免每次点击都白等 TOKEN_WAIT_MS。
function patchToken(auth, workspaceAuth, team) {
  if (typeof auth?.getWorkspaceAuthToken !== 'function') return false
  if (auth.getWorkspaceAuthToken === state._tokenWrap) return true
  const realToken = auth.getWorkspaceAuthToken.bind(auth)
  const wrapped = async () => {
    let cached
    try {
      cached = workspaceAuth?.getWorkspaceToken()
    } catch {
      cached = undefined
    }
    if (cached) return cached
    if (state.tokenStubUntil && Date.now() < state.tokenStubUntil) return LOCAL_TOKEN
    let token
    try {
      token = await Promise.race([realToken(), wait(TOKEN_WAIT_MS)])
    } catch {
      token = undefined
    }
    if (token || !team?.activeWorkspaceId) return token
    state.stubs += 1
    state.tokenStubUntil = Date.now() + TOKEN_STUB_TTL_MS
    return LOCAL_TOKEN
  }
  state._tokenWrap = wrapped
  auth.getWorkspaceAuthToken = wrapped
  return true
}

// 工作区切换：本地提交不依赖切换结果，限时等待并吞掉错误
function patchSwitch(team) {
  if (typeof team?.waitForWorkspaceSwitch !== 'function') return false
  if (team.waitForWorkspaceSwitch === state._switchWrap) return true
  const realWait = team.waitForWorkspaceSwitch.bind(team)
  const wrapped = async () => {
    try {
      await Promise.race([realWait(), wait(SWITCH_WAIT_MS)])
    } catch {
      // 忽略：本地提交不依赖工作区切换结果
    }
  }
  state._switchWrap = wrapped
  team.waitForWorkspaceSwitch = wrapped
  return true
}

function patchStores() {
  const a = patchToken(store('auth'), store('workspaceAuth'), store('teamWorkspace'))
  const b = patchSwitch(store('teamWorkspace'))
  if ((a || b) && !state.storesPatched) {
    state.storesPatched = true
    console.info('[yyp-local-queue-fix] stores wrapped: token stub + switch timeout')
  }
  return a || b
}

function resetStuck(reason) {
  const app = window.app
  app.processingQueue = false
  if (Array.isArray(app.queueItems)) app.queueItems.length = 0
  state.watchdogHits += 1
  console.warn(`[yyp-local-queue-fix] ${reason}, reset queue state`)
}

function install(app) {
  if (app.__yypQueueFix) return true
  const original = app.queuePrompt
  if (typeof original !== 'function') return false
  app.__yypQueueFix = true
  app.queuePrompt = async function (...args) {
    patchStores()
    const now = Date.now()
    if (app.processingQueue && (now - state.lastSubmitAt >= STUCK_RESET_MS || !state.lastSubmitAt)) {
      // 有提交挂着但远超正常耗时：视为挂死，复位后让本次点击立即接管
      resetStuck(`previous submit stuck for ${state.lastSubmitAt ? Math.round((now - state.lastSubmitAt) / 1000) + 's' : 'unknown time'}`)
    }
    if (!app.processingQueue) {
      // 本次调用是发起者：记录开始时间并设兜底看门狗
      state.lastSubmitAt = Date.now()
      const timer = setTimeout(() => {
        if (app.processingQueue && Date.now() - state.lastSubmitAt >= STUCK_RESET_MS) {
          resetStuck('watchdog: submit did not finish')
        }
      }, WATCHDOG_MS)
      try {
        return await original.apply(app, args)
      } finally {
        clearTimeout(timer)
      }
    }
    // 正常单飞窗口内的重复点击：维持上游语义（push 排队或被拒绝）
    return original.apply(app, args)
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
    console.info('[yyp-local-queue-fix] active: fetch timeouts + token stub + stuck-submit takeover')
  }
  return true
}

if (!CLOUD_HOST_RE.test(location.hostname)) {
  patchCloudFetch()
  if (!bootstrap()) {
    const poll = setInterval(() => {
      if (bootstrap()) clearInterval(poll)
    }, 300)
    setTimeout(() => clearInterval(poll), 300_000)
  }
}
