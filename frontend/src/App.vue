<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { displayTime } from './time.js'
import { LANG_OPTIONS, currentLang, setCurrentLang, t, translateAmsat } from './i18n.js'
import { gridCenter } from './locator.js'

const user = ref(null), page = ref('passes'), error = ref(''), notice = ref(''), busy = ref(false)
const auth = reactive({ username: '', password: '', invite: '' }), registerMode = ref('closed'), signingUp = ref(false)
const station = reactive({ latitude: 0, longitude: 0, altitude_m: 0, timezone: 'Asia/Shanghai', locator: '' }), barkKey = ref('')
const notifyForm = reactive({
  bark: { enabled: true, key: '', clear: false },
  serverchan: { enabled: false, sendkey: '', clear: false },
  pushplus: { enabled: false, token: '', topic: '', clear: false },
  telegram: { enabled: false, bot_token: '', chat_id: '', clear: false },
  ntfy: { enabled: false, server_url: 'https://ntfy.sh', topic: '', token: '', clear: false },
  bot_webhook: { enabled: false, url: '', clear: false },
  webhook: { enabled: false, url: '', token: '', clear: false }
})

function syncNotifyForm(u) {
  const ch = u?.notify_channels || {}
  if (ch.bark) notifyForm.bark.enabled = !!ch.bark.enabled
  if (ch.serverchan) notifyForm.serverchan.enabled = !!ch.serverchan.enabled
  if (ch.pushplus) {
    notifyForm.pushplus.enabled = !!ch.pushplus.enabled
    notifyForm.pushplus.topic = ch.pushplus.topic || ''
  }
  if (ch.telegram) {
    notifyForm.telegram.enabled = !!ch.telegram.enabled
    notifyForm.telegram.chat_id = ch.telegram.chat_id || ''
  }
  if (ch.ntfy) {
    notifyForm.ntfy.enabled = !!ch.ntfy.enabled
    notifyForm.ntfy.server_url = ch.ntfy.server_url || 'https://ntfy.sh'
    notifyForm.ntfy.topic = ch.ntfy.topic || ''
  }
  if (ch.bot_webhook) notifyForm.bot_webhook.enabled = !!ch.bot_webhook.enabled
  if (ch.webhook) notifyForm.webhook.enabled = !!ch.webhook.enabled
}

async function saveNotifyChannels() {
  await action(async () => {
    await api('/me/notify', 'PUT', { ...notifyForm, lang: currentLang.value })
    const u = await api('/me')
    setUser(u)
    syncNotifyForm(u)
    notifyForm.bark.key = ''
    notifyForm.serverchan.sendkey = ''
    notifyForm.pushplus.token = ''
    notifyForm.telegram.bot_token = ''
    notifyForm.ntfy.token = ''
    notifyForm.bot_webhook.url = ''
    notifyForm.webhook.url = ''
    notifyForm.webhook.token = ''
    for (const k of Object.keys(notifyForm)) notifyForm[k].clear = false
  }, t('channel_label_13'))
}

async function testNotifyChannel(channel = null) {
  await action(async () => {
    const res = await api('/me/notify/test', 'POST', channel ? { channel } : {})
    notice.value = res.errors?.length ? t('test_partial', { err: res.errors.join('; ') }) : t('test_sent')
  })
}
const satellites = ref([]), subscriptions = ref([]), passes = ref([]), warnings = ref([]), logs = ref([]), users = ref([])
const hours = ref(24), norad = ref(25544), amsatName = ref('ISS_[FM]'), orbitText = ref(''), orbitFormat = ref('tle'), inviteCode = ref(''), worker = ref(null)
const gridError = ref('')

// 新增：实时时钟与筛选/搜索状态
const nowMs = ref(Date.now())
const filterSat = ref('all')
const filterMinEl = ref(0)
const hidePast = ref(true)
const satQuery = ref('')
const satOnlyEnabled = ref(false)

let clockTimer = null

function updateGrid() {
  gridError.value = ''
  if (!station.locator?.trim()) return
  try {
    const converted = gridCenter(station.locator)
    station.latitude = converted.latitude
    station.longitude = converted.longitude
  } catch (e) {
    gridError.value = e.message
  }
}

function manualCoordinates() {
  station.locator = ''
  gridError.value = ''
}

const selectedCount = computed(() => subscriptions.value.filter(s => s.enabled).length)

// 过境卡片筛选与下拉选项
const passSatOptions = computed(() => {
  const map = new Map()
  for (const p of passes.value) {
    if (!map.has(p.norad_id)) map.set(p.norad_id, p.name)
  }
  return Array.from(map.entries()).map(([norad_id, name]) => ({ norad_id, name }))
})

const filteredPasses = computed(() => {
  return passes.value.filter(p => {
    if (filterSat.value !== 'all' && String(p.norad_id) !== String(filterSat.value)) return false
    if (Number(p.max_elevation) < filterMinEl.value) return false
    if (hidePast.value && new Date(p.los).getTime() < nowMs.value) return false
    return true
  })
})

const filteredSatellites = computed(() => {
  const q = satQuery.value.trim().toLowerCase()
  return satellites.value.filter(s => {
    if (satOnlyEnabled.value && !s.rule?.enabled) return false
    if (!q) return true
    return s.name.toLowerCase().includes(q) || String(s.norad_id).includes(q)
  })
})

// 方位角转罗盘缩写
function compassDir(deg) {
  if (deg === null || deg === undefined || Number.isNaN(Number(deg))) return ''
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
  const idx = Math.round(((Number(deg) % 360) + 360) % 360 / 22.5) % 16
  return dirs[idx]
}

// 格式化秒数为时分秒
function formatDuration(sec) {
  const total = Math.max(0, Math.floor(sec))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`
  return `${s}s`
}

// 单次过境实时状态与倒计时
function passStatus(p) {
  const aos = new Date(p.aos).getTime()
  const los = new Date(p.los).getTime()
  const now = nowMs.value
  const totalSec = Math.max(1, (los - aos) / 1000)

  if (now > los) {
    return { state: 'past', label: '已过境', detail: `时长 ${formatDuration(totalSec)}` }
  }
  if (now >= aos && now <= los) {
    const rem = (los - now) / 1000
    return { state: 'active', label: '过境中 · 信号窗口', detail: `距离落下 ${formatDuration(rem)}` }
  }
  const waitSec = (aos - now) / 1000
  if (waitSec <= 900) {
    return { state: 'soon', label: `即将升起 · ${formatDuration(waitSec)}`, detail: `过境时长 ${formatDuration(totalSec)}` }
  }
  return { state: 'upcoming', label: `${formatDuration(waitSec)} 后升起`, detail: `过境时长 ${formatDuration(totalSec)}` }
}

// 计算极坐标雷达图坐标 (中心 62,62 半径 48)
function polarPoint(azDeg, elDeg) {
  const r = ((90 - Math.max(0, Math.min(90, Number(elDeg) || 0))) / 90) * 48
  const rad = ((Number(azDeg) || 0) - 90) * (Math.PI / 180)
  return {
    x: +(62 + r * Math.cos(rad)).toFixed(1),
    y: +(62 + r * Math.sin(rad)).toFixed(1)
  }
}

function skyplotData(p) {
  const aosPt = polarPoint(p.aos_azimuth, 0)
  const losPt = polarPoint(p.los_azimuth, 0)
  let peakAz = p.peak_azimuth
  if (peakAz == null) {
    const a1 = (Number(p.aos_azimuth) || 0) * (Math.PI / 180)
    const a2 = (Number(p.los_azimuth) || 0) * (Math.PI / 180)
    peakAz = ((Math.atan2(Math.sin(a1) + Math.sin(a2), Math.cos(a1) + Math.cos(a2)) * 180) / Math.PI + 360) % 360
  }
  const peakPt = polarPoint(peakAz, p.max_elevation)
  if (Array.isArray(p.track) && p.track.length >= 2) {
    const pts = p.track.map(([az, el]) => polarPoint(az, el))
    const path = pts.map((pt, idx) => `${idx === 0 ? 'M' : 'L'} ${pt.x} ${pt.y}`).join(' ')
    return { aos: pts[0], peak: peakPt, los: pts[pts.length - 1], path }
  }
  const ctrlX = +(2 * peakPt.x - 0.5 * (aosPt.x + losPt.x)).toFixed(1)
  const ctrlY = +(2 * peakPt.y - 0.5 * (aosPt.y + losPt.y)).toFixed(1)
  return {
    aos: aosPt,
    peak: peakPt,
    los: losPt,
    path: `M ${aosPt.x} ${aosPt.y} Q ${ctrlX} ${ctrlY} ${losPt.x} ${losPt.y}`
  }
}

async function api(path, method = 'GET', body) {
  const res = await fetch('/api/v1' + path, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-SPA-Request': '1' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  })
  const data = await res.json()
  if (!res.ok) {
    if (res.status === 401) user.value = null
    throw new Error(typeof data.detail === 'string' ? data.detail : '输入无效，请检查字段范围')
  }
  return data
}

async function action(fn, message = '') {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await fn()
    if (message) notice.value = message
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function saveLanguage(code) {
  if (user.value) {
    const res = await api('/me/notify', 'PUT', { lang: code })
    user.value.lang = res.lang
  }
  setCurrentLang(code)
}

async function switchLanguage(code) {
  await action(() => saveLanguage(code))
}

function setUser(value) {
  user.value = value
  if (value.lang) setCurrentLang(value.lang)
  syncNotifyForm(value)
  for (const key in station) {
    station[key] = value[key] ?? (key === 'locator' ? '' : station[key])
  }
}

async function refresh() {
  const [sat, sub, p, l] = await Promise.all([
    api('/satellites'),
    api('/subscriptions'),
    api('/passes?hours=' + hours.value),
    api('/notifications')
  ])
  subscriptions.value = sub
  satellites.value = sat.map(s => ({
    ...s,
    rule: { min_elevation: 10, lead_minutes: 10, enabled: false, ...sub.find(x => x.norad_id === s.norad_id) }
  }))
  passes.value = p.passes
  warnings.value = p.warnings
  logs.value = l
  if (user.value?.is_admin) {
    users.value = await api('/admin/users')
    worker.value = await api('/admin/status')
  }
}

async function login() {
  await action(async () => {
    setUser(await api('/auth/login', 'POST', auth))
    if (!user.value.lang) await saveLanguage(currentLang.value)
    auth.password = ''
    await refresh()
  })
}

async function register() {
  await action(async () => {
    const r = await api('/auth/register', 'POST', auth)
    signingUp.value = false
    notice.value = r.approved ? '注册成功，请登录' : '注册已提交，等待管理员审核'
  })
}

async function saveStation() {
  if (gridError.value) return
  await action(async () => {
    setUser(await api('/me', 'PUT', station))
    await refresh()
  }, '电台站设置已保存')
}

async function saveRule(s) {
  await action(async () => {
    await api('/subscriptions/' + s.norad_id, 'PUT', s.rule)
    await refresh()
  }, '订阅规则已保存')
}

async function deleteSatellite(s) {
  if (!window.confirm(`确定要从目录中删除「${s.name}」(NORAD ${s.norad_id}) 吗？\n删除后将同时清除所有用户的该卫星订阅及待发送提醒。`)) return
  await action(async () => {
    await api('/admin/satellites/' + s.norad_id, 'DELETE')
    await refresh()
  }, `已删除卫星 ${s.name}`)
}

const tleSource = ref('auto')

async function fetchOrbit() {
  await action(async () => {
    await api('/admin/satellites/fetch', 'POST', { norad_id: norad.value, amsat_name: amsatName.value || null, source: tleSource.value })
    await refresh()
  }, '卫星轨道已更新')
}

async function syncDefaultSatellites() {
  await action(async () => {
    const res = await api('/admin/satellites/sync-defaults', 'POST', { source: tleSource.value })
    await refresh()
    notice.value = `已同步 ${res.synced} 颗常用业余卫星`
  })
}

async function importOrbit() {
  await action(async () => {
    await api('/admin/satellites/import', 'POST', { elements: orbitText.value, format: orbitFormat.value, amsat_name: amsatName.value || null })
    orbitText.value = ''
    await refresh()
  }, '轨道已导入')
}

function time(v) {
  return displayTime(v, user.value?.timezone)
}

onMounted(async () => {
  setCurrentLang(currentLang.value)
  clockTimer = setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)

  await action(async () => {
    registerMode.value = (await api('/auth/options')).registration_mode
    const res = await fetch('/api/v1/me')
    if (res.ok) {
      setUser(await res.json())
      if (!user.value.lang) await saveLanguage(currentLang.value)
      await refresh()
    }
  })
})

onUnmounted(() => {
  if (clockTimer) clearInterval(clockTimer)
})
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <a class="brand" href="#">{{ '◉' }} <strong>SPA</strong><span>Sat Pass Alert</span></a>
      <div class="sidebar-caption">AMATEUR RADIO · ORBIT WATCH</div>
      <nav v-if="user" aria-label="主导航">
        <button
          v-for="item in [{id:'passes',name:t('nav_passes')},{id:'satellites',name:t('nav_satellites')},{id:'station',name:t('nav_station')},{id:'logs',name:t('nav_logs')},...(user.is_admin?[{id:'admin',name:t('nav_admin')}]:[])]"
          :key="item.id"
          :class="{ active: page === item.id }"
          @click="page = item.id"
        >{{ item.name }}</button>
      </nav>
      <div class="sidebar-foot">为下一次通联做好准备。<br><small>SGP4 轨道预测 · 自托管</small></div>
    </aside>

    <main>
      <header class="topbar">
        <span>{{ t('app_title') }}</span>
        <div style="display:flex;align-items:center;gap:10px">
          <div class="lang-switch" role="group" aria-label="Language" style="display:inline-flex;gap:4px">
            <button v-for="opt in LANG_OPTIONS" :key="opt.code" type="button" class="quiet"
              :style="currentLang === opt.code ? 'font-weight:700;text-decoration:underline;opacity:1' : 'opacity:0.65'"
              :disabled="busy" @click="switchLanguage(opt.code)">{{ opt.label }}</button>
          </div>
          <div v-if="user">{{ user.username }} <button class="quiet" @click="action(async () => { await api('/auth/logout', 'POST'); user = null })">{{ t('logout') }}</button></div>
          <span v-else>SPA / v0.1</span>
        </div>
      </header>

      <p v-if="error" class="alert error" role="alert">{{ error }}</p>
      <p v-if="notice" class="alert success" role="status">{{ notice }}</p>
      <p v-if="busy" class="loading" role="status">{{ t('processing') }}</p>

      <section v-if="!user" class="login">
        <div class="eyebrow">YOUR STATION. YOUR SKY.</div>
        <h1>{{ t('login_title') }}</h1>
        <p class="muted">{{ t('login_desc') }}</p>
        <form class="panel auth" @submit.prevent="signingUp ? register() : login()">
          <h2>{{ signingUp ? t('create_account') : t('login_station') }}</h2>
          <label>{{ t('username') }}<input v-model="auth.username" autocomplete="username" required minlength="3" maxlength="64" pattern="[A-Za-z0-9_.-]+"></label>
          <label>{{ t('password') }}<input v-model="auth.password" type="password" :autocomplete="signingUp ? 'new-password' : 'current-password'" required minlength="12" maxlength="128"></label>
          <label v-if="signingUp && registerMode === 'invite'">{{ t('invite_code') }}<input v-model="auth.invite" required></label>
          <button :disabled="busy" class="primary">{{ signingUp ? t('submit_register') : t('login_btn') }}</button>
          <button v-if="registerMode !== 'closed'" type="button" class="quiet" @click="signingUp = !signingUp">{{ signingUp ? t('back_login') : t('create_account') }}</button>
          <p v-else class="muted">{{ t('reg_closed_hint') }}</p>
        </form>
      </section>

      <template v-else>
        <!-- 1. 过境预报 -->
        <section v-if="page === 'passes'">
          <div class="section-head">
            <div>
              <div class="eyebrow">PASS FORECAST</div>
              <h1>{{ t('nav_passes') }}</h1>
              <p class="muted">{{ t('passes_subtitle', { tz: user.timezone }) }}</p>
            </div>
            <button :disabled="busy" @click="action(refresh)">{{ t('refresh_passes') }}</button>
          </div>

          <div class="stats">
            <div><span>{{ t('stat_enabled') }}</span><strong>{{ selectedCount }} <small>{{ t('stat_enabled_unit') }}</small></strong></div>
            <div><span>{{ t('stat_filtered') }}</span><strong>{{ filteredPasses.length }} <small>{{ t('stat_filtered_unit', { total: passes.length }) }}</small></strong></div>
            <div><span>{{ t('stat_location') }}</span><strong class="coordinate">{{ user.locator || `${user.latitude}°, ${user.longitude}°` }}</strong></div>
          </div>

          <!-- 筛选栏 -->
          <div class="panel filter-bar">
            <label class="inline">{{ t('filter_range') }}
              <select v-model.number="hours" @change="action(refresh)">
                <option :value="24">{{ t('hours_24') }}</option>
                <option :value="48">{{ t('hours_48') }}</option>
                <option :value="72">{{ t('hours_72') }}</option>
              </select>
            </label>
            <label class="inline">{{ t('filter_sat') }}
              <select v-model="filterSat">
                <option value="all">{{ t('all_subscribed') }}</option>
                <option v-for="opt in passSatOptions" :key="opt.norad_id" :value="opt.norad_id">{{ opt.name }} ({{ opt.norad_id }})</option>
              </select>
            </label>
            <label class="inline">{{ t('filter_min_el') }}
              <select v-model.number="filterMinEl">
                <option :value="0">{{ t('el_any') }}</option>
                <option :value="20">≥ 20°</option>
                <option :value="30">{{ t('el_30') }}</option>
                <option :value="45">{{ t('el_45') }}</option>
                <option :value="60">{{ t('el_60') }}</option>
              </select>
            </label>
            <label class="check inline"><input v-model="hidePast" type="checkbox">{{ t('hide_past') }}</label>
          </div>

          <p v-for="w in warnings" :key="w" class="alert error">{{ w }}</p>

          <div v-if="!filteredPasses.length" class="panel empty">
            <h2>{{ t('no_passes_title') }}</h2>
            <p>{{ t('no_passes_desc') }}</p>
            <button @click="page = 'satellites'">{{ t('manage_subs') }}</button>
          </div>

          <article
            v-for="p in filteredPasses"
            :key="p.norad_id + p.aos"
            class="pass-card"
            :class="'status-' + passStatus(p).state"
          >
            <div class="pass-meta">
              <div class="pass-badges">
                <span class="eyebrow">NORAD {{ p.norad_id }}</span>
                <span class="status-pill" :class="passStatus(p).state">{{ passStatus(p).label }}</span>
              </div>
              <h2>{{ p.name }}</h2>
              <span class="muted">AMSAT · {{ translateAmsat(p.amsat_status) }} · {{ passStatus(p).detail }}</span>
            </div>

            <div>
              <span class="muted">{{ t('aos_label') }}</span>
              <strong>{{ time(p.aos) }}</strong>
              <small>{{ t('az_label', { az: p.aos_azimuth, dir: compassDir(p.aos_azimuth) }) }}</small>
            </div>

            <div>
              <span class="muted">{{ t('max_el_label') }}</span>
              <strong class="accent">{{ p.max_elevation }}°</strong>
              <small>{{ time(p.peak_at) }}</small>
            </div>

            <div>
              <span class="muted">{{ t('los_label') }}</span>
              <strong>{{ time(p.los) }}</strong>
              <small>{{ t('az_label', { az: p.los_azimuth, dir: compassDir(p.los_azimuth) }) }}</small>
            </div>

            <!-- SVG 极坐标雷达图 -->
            <div class="skyplot-wrap" :title="t('skyplot_tip')">
              <svg viewBox="0 0 124 124" class="skyplot" :aria-label="t('channel_label_12')">
                <circle cx="62" cy="62" r="48" class="sky-ring" />
                <circle cx="62" cy="62" r="32" class="sky-ring dashed" />
                <circle cx="62" cy="62" r="16" class="sky-ring dashed" />
                <line x1="62" y1="14" x2="62" y2="110" class="sky-axis" />
                <line x1="14" y1="62" x2="110" y2="62" class="sky-axis" />
                <text x="62" y="10" class="sky-label">N</text>
                <text x="117" y="65" class="sky-label">E</text>
                <text x="62" y="121" class="sky-label">S</text>
                <text x="7" y="65" class="sky-label">W</text>
                <path :d="skyplotData(p).path" class="sky-arc" />
                <circle :cx="skyplotData(p).aos.x" :cy="skyplotData(p).aos.y" r="3.2" class="sky-dot aos" />
                <circle :cx="skyplotData(p).peak.x" :cy="skyplotData(p).peak.y" r="3.5" class="sky-dot peak" />
                <circle :cx="skyplotData(p).los.x" :cy="skyplotData(p).los.y" r="3.2" class="sky-dot los" />
              </svg>
            </div>
          </article>

          <p class="muted footnote">{{ t('footnote') }}</p>
        </section>

        <!-- 2. 卫星订阅 -->
        <section v-if="page === 'satellites'">
          <div class="eyebrow">SATELLITE CATALOG</div>
          <h1>{{ t('nav_satellites') }}</h1>
          <p class="muted">{{ t('sat_desc') }}</p>

          <div class="panel filter-bar">
            <label class="inline search-box">{{ t('search_sat') }}
              <input v-model="satQuery" type="search" :placeholder="t('search_sat_ph')">
            </label>
            <label class="check inline"><input v-model="satOnlyEnabled" type="checkbox">{{ t('only_enabled', { count: selectedCount }) }}</label>
          </div>

          <div v-if="!satellites.length" class="panel empty">{{ t('sat_empty') }}</div>
          <div v-else-if="!filteredSatellites.length" class="panel empty">{{ t('sat_no_match') }}</div>

          <form v-for="s in filteredSatellites" :key="s.norad_id" class="panel satellite" @submit.prevent="saveRule(s)">
            <div>
              <h2>{{ s.name }}</h2>
              <p class="muted">NORAD {{ s.norad_id }} · {{ t('orbit_epoch') }} {{ time(s.epoch) }}</p>
            </div>
            <label>{{ t('rule_min_el') }}<input v-model.number="s.rule.min_elevation" type="number" min="0" max="90" step="1" required></label>
            <label>{{ t('rule_lead') }}<input v-model.number="s.rule.lead_minutes" type="number" min="1" max="120" required></label>
            <label class="check"><input v-model="s.rule.enabled" type="checkbox">{{ t('rule_enabled') }}</label>
            <div class="sat-actions">
              <button class="primary" :disabled="busy">{{ t('save') }}</button>
              <button v-if="user.is_admin" type="button" class="danger" :disabled="busy" @click="deleteSatellite(s)">{{ t('delete') }}</button>
            </div>
          </form>
        </section>

        <!-- 3. 观测站与推送 -->
        <section v-if="page === 'station'">
          <div class="eyebrow">STATION SETTINGS</div>
          <h1>{{ t('nav_station') }}</h1>
          <div class="columns">
            <form class="panel" @submit.prevent="saveStation">
              <h2>{{ t('obs_location') }}</h2>
              <p class="muted">{{ t('lat_lon_hint') }}</p>
              <label>{{ t('grid_label') }}
                <input v-model="station.locator" @input="updateGrid" :placeholder="t('grid_ph')" maxlength="8" autocomplete="off" spellcheck="false" :aria-invalid="!!gridError" aria-describedby="grid-help">
              </label>
              <p id="grid-help" class="muted">{{ t('grid_help') }}</p>
              <p v-if="gridError" class="alert error" role="alert">{{ gridError }}</p>
              <label>{{ t('latitude') }}<input @input="manualCoordinates" v-model.number="station.latitude" type="number" min="-90" max="90" step="any" required></label>
              <label>{{ t('longitude') }}<input @input="manualCoordinates" v-model.number="station.longitude" type="number" min="-180" max="180" step="any" required></label>
              <label>{{ t('altitude') }}<input v-model.number="station.altitude_m" type="number" min="-500" max="10000" step="any" required></label>
              <label>{{ t('timezone') }}<input v-model="station.timezone" placeholder="Asia/Shanghai" required></label>
              <button class="primary" :disabled="busy || !!gridError">{{ t('save_station') }}</button>
            </form>

            <form class="panel" @submit.prevent="saveNotifyChannels">
  <h2>{{ t('multi_push') }}</h2>
  <span class="badge">{{ user.bark_configured ? t('configured_badge') : t('unconfigured') }}</span>
  <p class="muted">{{ t('multi_push_desc') }}</p><p class="muted">{{ t('push_lang_hint', { lang: currentLang }) }}</p>
  <details open class="notify-group">
    <summary><strong>Bark（iOS / macOS）</strong> ·
      <small>{{ user.notify_channels?.bark?.configured ? t('configured') : t('unconfigured') }}</small>
    </summary>
    <label class="check">
      <input v-model="notifyForm.bark.enabled" type="checkbox">{{ t('enable_channel', { channel: 'Bark' }) }}
    </label>
    <label>{{ t('channel_label_0') }}
      <input v-model="notifyForm.bark.key" type="password"
        placeholder="Key 或 https://api.day.app/Key/" autocomplete="off">
    </label>
    <label v-if="user.notify_channels?.bark?.configured" class="check">
      <input v-model="notifyForm.bark.clear" type="checkbox">{{ t('clear_secret') }}
    </label>
    <button type="button"
      :disabled="busy || !user.notify_channels?.bark?.configured"
      @click="testNotifyChannel('bark')">{{ t('test_channel', { channel: 'Bark' }) }}</button>
  </details>
  <details class="notify-group">
    <summary><strong>Server酱 Turbo（微信）</strong> ·
      <small>{{ user.notify_channels?.serverchan?.configured ? t('configured') : t('unconfigured') }}</small>
    </summary>
    <label class="check">
      <input v-model="notifyForm.serverchan.enabled" type="checkbox">{{ t('enable_channel', { channel: 'Server酱' }) }}
    </label>
    <label>SendKey
      <input v-model="notifyForm.serverchan.sendkey" type="password"
        placeholder="SCT..." autocomplete="off">
    </label>
    <label v-if="user.notify_channels?.serverchan?.configured" class="check">
      <input v-model="notifyForm.serverchan.clear" type="checkbox">{{ t('clear_secret') }}
    </label>
    <button type="button"
      :disabled="busy || !user.notify_channels?.serverchan?.configured"
      @click="testNotifyChannel('serverchan')">{{ t('test_channel', { channel: 'Server酱' }) }}</button>
  </details>
  <details class="notify-group">
    <summary><strong>PushPlus 推送加</strong> ·
      <small>{{ user.notify_channels?.pushplus?.configured ? t('configured') : t('unconfigured') }}</small>
    </summary>
    <label class="check">
      <input v-model="notifyForm.pushplus.enabled" type="checkbox">{{ t('enable_channel', { channel: 'PushPlus' }) }}
    </label>
    <label>Token
      <input v-model="notifyForm.pushplus.token" type="password" autocomplete="off">
    </label>
    <label>{{ t('channel_label_1') }}
      <input v-model="notifyForm.pushplus.topic" placeholder="一对一留空">
    </label>
    <label v-if="user.notify_channels?.pushplus?.configured" class="check">
      <input v-model="notifyForm.pushplus.clear" type="checkbox">{{ t('clear_secret') }}
    </label>
    <button type="button"
      :disabled="busy || !user.notify_channels?.pushplus?.configured"
      @click="testNotifyChannel('pushplus')">{{ t('test_channel', { channel: 'PushPlus' }) }}</button>
  </details>
  <details class="notify-group">
    <summary><strong>Telegram Bot</strong> ·
      <small>{{ user.notify_channels?.telegram?.configured ? t('configured') : t('unconfigured') }}</small>
    </summary>
    <label class="check">
      <input v-model="notifyForm.telegram.enabled" type="checkbox">{{ t('enable_channel', { channel: 'Telegram' }) }}
    </label>
    <label>Bot Token
      <input v-model="notifyForm.telegram.bot_token" type="password"
        placeholder="123456:ABC..." autocomplete="off">
    </label>
    <label>Chat ID
      <input v-model="notifyForm.telegram.chat_id" placeholder="用户或群组 ID">
    </label>
    <label v-if="user.notify_channels?.telegram?.configured" class="check">
      <input v-model="notifyForm.telegram.clear" type="checkbox">{{ t('clear_secret') }}
    </label>
    <button type="button"
      :disabled="busy || !user.notify_channels?.telegram?.configured"
      @click="testNotifyChannel('telegram')">{{ t('test_channel', { channel: 'Telegram' }) }}</button>
  </details>
  <details class="notify-group">
    <summary><strong>ntfy（自建 / 官方）</strong> ·
      <small>{{ user.notify_channels?.ntfy?.configured ? t('configured') : t('unconfigured') }}</small>
    </summary>
    <label class="check">
      <input v-model="notifyForm.ntfy.enabled" type="checkbox">{{ t('enable_channel', { channel: 'ntfy' }) }}
    </label>
    <label>{{ t('channel_label_2') }}
      <input v-model="notifyForm.ntfy.server_url" placeholder="https://ntfy.sh">
    </label>
    <label>{{ t('channel_label_3') }}
      <input v-model="notifyForm.ntfy.topic" placeholder="ba8blk-sat">
    </label>
    <label>{{ t('channel_label_4') }}
      <input v-model="notifyForm.ntfy.token" type="password" autocomplete="off">
    </label>
    <label v-if="user.notify_channels?.ntfy?.configured" class="check">
      <input v-model="notifyForm.ntfy.clear" type="checkbox">{{ t('clear_cfg') }}
    </label>
    <button type="button"
      :disabled="busy || !user.notify_channels?.ntfy?.configured"
      @click="testNotifyChannel('ntfy')">{{ t('test_channel', { channel: 'ntfy' }) }}</button>
  </details>
  <details class="notify-group">
    <summary><strong>{{ t('channel_label_10') }}</strong> ·
      <small>{{ user.notify_channels?.bot_webhook?.configured ? t('configured') : t('unconfigured') }}</small>
    </summary>
    <label class="check">
      <input v-model="notifyForm.bot_webhook.enabled" type="checkbox">启用群机器人
    </label>
    <label>Webhook URL
      <input v-model="notifyForm.bot_webhook.url" type="password"
        placeholder="https://qyapi.weixin.qq.com/..." autocomplete="off">
    </label>
    <label v-if="user.notify_channels?.bot_webhook?.configured" class="check">
      <input v-model="notifyForm.bot_webhook.clear" type="checkbox">{{ t('clear_secret') }}
    </label>
    <button type="button"
      :disabled="busy || !user.notify_channels?.bot_webhook?.configured"
      @click="testNotifyChannel('bot_webhook')">测试群机器人</button>
  </details>
  <details class="notify-group">
    <summary><strong>{{ t('channel_label_9') }}</strong> ·
      <small>{{ user.notify_channels?.webhook?.configured ? t('configured') : t('unconfigured') }}</small>
    </summary>
    <label class="check">
      <input v-model="notifyForm.webhook.enabled" type="checkbox">启用自定义 Webhook
    </label>
    <label>{{ t('channel_label_5') }}
      <input v-model="notifyForm.webhook.url" type="password"
        placeholder="https://example.com/hook" autocomplete="off">
    </label>
    <label>{{ t('channel_label_6') }}
      <input v-model="notifyForm.webhook.token" type="password" autocomplete="off">
    </label>
    <label v-if="user.notify_channels?.webhook?.configured" class="check">
      <input v-model="notifyForm.webhook.clear" type="checkbox">{{ t('clear_secret') }}
    </label>
    <button type="button"
      :disabled="busy || !user.notify_channels?.webhook?.configured"
      @click="testNotifyChannel('webhook')">{{ t('test_channel', { channel: 'Webhook' }) }}</button>
  </details>
  <div style="margin-top:16px">
    <button class="primary" :disabled="busy">{{ t('save_all_push') }}</button>
    <button type="button" :disabled="busy || !user.bark_configured"
      @click="testNotifyChannel()">{{ t('test_all_push') }}</button>
  </div>
</form>
          </div>
        </section>

        <!-- 4. 通知记录 -->
        <section v-if="page === 'logs'">
          <div class="eyebrow">DELIVERY HISTORY</div>
          <h1>{{ t('nav_logs') }}</h1>
          <p class="muted">{{ t('logs_desc') }}</p>
          <div class="panel empty" v-if="!logs.length">{{ t('logs_empty') }}</div>
          <div class="table-wrap" v-else>
            <table>
              <thead>
                <tr><th>{{ t('col_notif') }}</th><th>{{ t('col_due') }}</th><th>{{ t('col_state') }}</th><th>{{ t('col_attempts') }}</th><th>{{ t('col_note') }}</th></tr>
              </thead>
              <tbody>
                <tr v-for="n in logs" :key="n.id">
                  <td>{{ n.title }}</td>
                  <td>{{ time(n.due_at) }}</td>
                  <td><span class="badge">{{ n.state }}</span></td>
                  <td>{{ n.attempts }}</td>
                  <td>{{ n.last_error || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- 5. 系统管理 -->
        <section v-if="page === 'admin' && user.is_admin">
          <div class="eyebrow">SYSTEM CONTROL</div>
          <h1>{{ t('nav_admin') }}</h1>
          <p class="muted">{{ t('worker_status', { state: worker?.worker_enabled ? t('enabled_txt') : t('disabled_txt'), time: time(worker?.last_tick_utc) }) }}</p>
          <div class="columns">
            <div class="panel">
              <h2>{{ t('add_refresh_sat') }}</h2>
              <form @submit.prevent="fetchOrbit">
                <label>{{ t('tle_source') }}
                  <select v-model="tleSource">
                    <option value="auto">{{ t('src_auto') }}</option>
                    <option value="amsat">{{ t('src_amsat') }}</option>
                    <option value="celestrak">{{ t('src_celestrak') }}</option>
                  </select>
                </label>
                <label>{{ t('norad_id') }}<input v-model.number="norad" type="number" min="1" required></label>
                <label>{{ t('amsat_exact') }}<input v-model="amsatName" placeholder="如 ARISS 或 SO-50 (SaudiSat-1C)"></label>
                <button class="primary" :disabled="busy">{{ t('fetch_update') }}</button>
                <button type="button" :disabled="busy" @click="syncDefaultSatellites">{{ t('sync_defaults') }}</button>
              </form>
              <details>
                <summary>{{ t('manual_import') }}</summary>
                <form @submit.prevent="importOrbit">
                  <label>{{ t('format') }}
                    <select v-model="orbitFormat">
                      <option value="tle">TLE</option>
                      <option value="omm">OMM JSON（单个对象）</option>
                    </select>
                  </label>
                  <label>{{ t('orbit_data') }}<textarea v-model="orbitText" rows="6" required></textarea></label>
                  <button :disabled="busy">{{ t('import_btn') }}</button>
                </form>
              </details>
            </div>

            <div class="panel">
              <h2>{{ t('reg_manage') }}</h2>
              <label>{{ t('reg_mode') }}
                <select v-model="registerMode">
                  <option value="closed">{{ t('mode_closed') }}</option>
                  <option value="invite">{{ t('mode_invite') }}</option>
                  <option value="approval">{{ t('mode_approval') }}</option>
                </select>
              </label>
              <button :disabled="busy" @click="action(() => api('/admin/registration', 'PUT', { mode: registerMode }), '注册方式已保存')">{{ t('save_mode') }}</button>
              <button :disabled="busy" @click="action(async () => { inviteCode = (await api('/admin/invites', 'POST')).invite })">{{ t('gen_invite') }}</button>
              <p v-if="inviteCode" class="code">{{ inviteCode }}<br><small>{{ t('invite_hint') }}</small></p>
              <h3>{{ t('user_approval') }}</h3>
              <div v-for="u in users" :key="u.id" class="user-row">
                <span>{{ u.username }} · {{ u.is_admin ? t('role_admin') : u.approved ? t('role_approved') : t('role_pending') }}</span>
                <button v-if="!u.is_admin" :disabled="busy" @click="action(async () => { await api('/admin/users/' + u.id + '/approval', 'PUT', { approved: !u.approved }); await refresh() })">{{ u.approved ? t('btn_disable') : t('btn_approve') }}</button>
              </div>
            </div>
          </div>
        </section>
      </template>

      <footer>SPA · SAT PASS ALERT <span>{{ t('footer_tag') }}</span></footer>
    </main>
  </div>
</template>