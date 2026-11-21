<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { displayTime } from './time.js'
import { gridCenter } from './locator.js'

const user = ref(null), page = ref('passes'), error = ref(''), notice = ref(''), busy = ref(false)
const auth = reactive({ username: '', password: '', invite: '' }), registerMode = ref('closed'), signingUp = ref(false)
const station = reactive({ latitude: 0, longitude: 0, altitude_m: 0, timezone: 'Asia/Shanghai', locator: '' }), barkKey = ref('')
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
  const peakAz = p.peak_azimuth ?? (((Number(p.aos_azimuth) + Number(p.los_azimuth)) / 2) % 360)
  const peakPt = polarPoint(peakAz, p.max_elevation)
  // 二次贝塞尔控制点使曲线经过峰值附近
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
    notice.value = message
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function setUser(value) {
  user.value = value
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
  }, '观测站已保存')
}

async function saveRule(s) {
  await action(async () => {
    await api('/subscriptions/' + s.norad_id, 'PUT', s.rule)
    await refresh()
  }, '订阅规则已保存')
}

async function fetchOrbit() {
  await action(async () => {
    await api('/admin/satellites/fetch', 'POST', { norad_id: norad.value, amsat_name: amsatName.value || null })
    await refresh()
  }, '卫星轨道已更新')
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
  clockTimer = setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)

  await action(async () => {
    registerMode.value = (await api('/auth/options')).registration_mode
    const res = await fetch('/api/v1/me')
    if (res.ok) {
      setUser(await res.json())
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
          v-for="item in [{id:'passes',name:'过境预报'},{id:'satellites',name:'卫星订阅'},{id:'station',name:'观测站与推送'},{id:'logs',name:'通知记录'},...(user.is_admin?[{id:'admin',name:'系统管理'}]:[])]"
          :key="item.id"
          :class="{ active: page === item.id }"
          @click="page = item.id"
        >{{ item.name }}</button>
      </nav>
      <div class="sidebar-foot">为下一次通联做好准备。<br><small>SGP4 轨道预测 · 自托管</small></div>
    </aside>

    <main>
      <header class="topbar">
        <span>卫星过境提醒</span>
        <div v-if="user">{{ user.username }} <button class="quiet" @click="action(async () => { await api('/auth/logout', 'POST'); user = null })">退出</button></div>
        <span v-else>SPA / v0.1</span>
      </header>

      <p v-if="error" class="alert error" role="alert">{{ error }}</p>
      <p v-if="notice" class="alert success" role="status">{{ notice }}</p>
      <p v-if="busy" class="loading" role="status">正在处理…</p>

      <section v-if="!user" class="login">
        <div class="eyebrow">YOUR STATION. YOUR SKY.</div>
        <h1>不错过下一次过境。</h1>
        <p class="muted">设置观测位置、订阅卫星，在升起前接收 Bark 提醒。</p>
        <form class="panel auth" @submit.prevent="signingUp ? register() : login()">
          <h2>{{ signingUp ? '创建账号' : '登录观测站' }}</h2>
          <label>用户名<input v-model="auth.username" autocomplete="username" required minlength="3" maxlength="64" pattern="[A-Za-z0-9_.-]+"></label>
          <label>密码<input v-model="auth.password" type="password" :autocomplete="signingUp ? 'new-password' : 'current-password'" required minlength="12" maxlength="128"></label>
          <label v-if="signingUp && registerMode === 'invite'">邀请码<input v-model="auth.invite" required></label>
          <button :disabled="busy" class="primary">{{ signingUp ? '提交注册' : '登录' }}</button>
          <button v-if="registerMode !== 'closed'" type="button" class="quiet" @click="signingUp = !signingUp">{{ signingUp ? '返回登录' : '创建账号' }}</button>
          <p v-else class="muted">本站已关闭注册，请联系管理员。</p>
        </form>
      </section>

      <template v-else>
        <!-- 1. 过境预报 -->
        <section v-if="page === 'passes'">
          <div class="section-head">
            <div>
              <div class="eyebrow">PASS FORECAST</div>
              <h1>过境预报</h1>
              <p class="muted">时间以 {{ user.timezone }} 显示 · 地平线升起 / 落下</p>
            </div>
            <button :disabled="busy" @click="action(refresh)">刷新预报</button>
          </div>

          <div class="stats">
            <div><span>已启用订阅</span><strong>{{ selectedCount }} <small>颗</small></strong></div>
            <div><span>符合筛选 / 总过境</span><strong>{{ filteredPasses.length }} <small>/ {{ passes.length }} 次</small></strong></div>
            <div><span>观测位置</span><strong class="coordinate">{{ user.locator || `${user.latitude}°, ${user.longitude}°` }}</strong></div>
          </div>

          <!-- 筛选栏 -->
          <div class="panel filter-bar">
            <label class="inline">预测范围
              <select v-model.number="hours" @change="action(refresh)">
                <option :value="24">未来 24 小时</option>
                <option :value="48">未来 48 小时</option>
                <option :value="72">未来 72 小时</option>
              </select>
            </label>
            <label class="inline">卫星筛选
              <select v-model="filterSat">
                <option value="all">全部已订阅卫星</option>
                <option v-for="opt in passSatOptions" :key="opt.norad_id" :value="opt.norad_id">{{ opt.name }} ({{ opt.norad_id }})</option>
              </select>
            </label>
            <label class="inline">最低最高仰角
              <select v-model.number="filterMinEl">
                <option :value="0">不限仰角</option>
                <option :value="20">≥ 20°</option>
                <option :value="30">≥ 30° (推荐)</option>
                <option :value="45">≥ 45° (高仰角)</option>
                <option :value="60">≥ 60° (过顶)</option>
              </select>
            </label>
            <label class="check inline"><input v-model="hidePast" type="checkbox">隐藏已过境</label>
          </div>

          <p v-for="w in warnings" :key="w" class="alert error">{{ w }}</p>

          <div v-if="!filteredPasses.length" class="panel empty">
            <h2>暂无符合当前筛选条件的过境</h2>
            <p>可放宽仰角/卫星筛选条件、取消“隐藏已过境”，或前往管理卫星订阅。</p>
            <button @click="page = 'satellites'">管理卫星订阅</button>
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
              <span class="muted">AMSAT · {{ p.amsat_status }} · {{ passStatus(p).detail }}</span>
            </div>

            <div>
              <span class="muted">升起 AOS</span>
              <strong>{{ time(p.aos) }}</strong>
              <small>方位 {{ p.aos_azimuth }}° {{ compassDir(p.aos_azimuth) }}</small>
            </div>

            <div>
              <span class="muted">最高仰角</span>
              <strong class="accent">{{ p.max_elevation }}°</strong>
              <small>{{ time(p.peak_at) }}</small>
            </div>

            <div>
              <span class="muted">落下 LOS</span>
              <strong>{{ time(p.los) }}</strong>
              <small>方位 {{ p.los_azimuth }}° {{ compassDir(p.los_azimuth) }}</small>
            </div>

            <!-- SVG 极坐标雷达图 -->
            <div class="skyplot-wrap" title="极坐标过境轨迹图（外圈 0°，内圈 30°/60°）">
              <svg viewBox="0 0 124 124" class="skyplot" aria-label="过境极坐标轨迹图">
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

          <p class="muted footnote">轨道预测存在误差。AMSAT 为用户报告，仅附加到通知，不作为推送过滤条件。轨迹图中绿点为 AOS、黄点为最高点、红点为 LOS。</p>
        </section>

        <!-- 2. 卫星订阅 -->
        <section v-if="page === 'satellites'">
          <div class="eyebrow">SATELLITE CATALOG</div>
          <h1>卫星订阅</h1>
          <p class="muted">按整次过境的最高仰角筛选，提醒时间相对地平线 AOS 提前。</p>

          <div class="panel filter-bar">
            <label class="inline search-box">搜索卫星
              <input v-model="satQuery" type="search" placeholder="输入卫星名称（如 ISS / RS-44）或 NORAD 编号…">
            </label>
            <label class="check inline"><input v-model="satOnlyEnabled" type="checkbox">仅显示已启用 ({{ selectedCount }})</label>
          </div>

          <div v-if="!satellites.length" class="panel empty">卫星目录为空。请管理员按 NORAD 编号添加或导入轨道。</div>
          <div v-else-if="!filteredSatellites.length" class="panel empty">没有匹配当前搜索条件的卫星。</div>

          <form v-for="s in filteredSatellites" :key="s.norad_id" class="panel satellite" @submit.prevent="saveRule(s)">
            <div>
              <h2>{{ s.name }}</h2>
              <p class="muted">NORAD {{ s.norad_id }} · 轨道历元 {{ time(s.epoch) }}</p>
            </div>
            <label>最低最高仰角（°）<input v-model.number="s.rule.min_elevation" type="number" min="0" max="90" step="1" required></label>
            <label>提前提醒（分钟）<input v-model.number="s.rule.lead_minutes" type="number" min="1" max="120" required></label>
            <label class="check"><input v-model="s.rule.enabled" type="checkbox">启用订阅</label>
            <button :disabled="busy">保存</button>
          </form>
        </section>

        <!-- 3. 观测站与推送 -->
        <section v-if="page === 'station'">
          <div class="eyebrow">STATION SETTINGS</div>
          <h1>观测站与推送</h1>
          <div class="columns">
            <form class="panel" @submit.prevent="saveStation">
              <h2>观测位置</h2>
              <p class="muted">纬度北正南负，经度东正西负。</p>
              <label>Maidenhead 网格定位（4 / 6 / 8 位）
                <input v-model="station.locator" @input="updateGrid" placeholder="例如 OM21、OM21EC、OM21EC24" maxlength="8" autocomplete="off" spellcheck="false" :aria-invalid="!!gridError" aria-describedby="grid-help">
              </label>
              <p id="grid-help" class="muted">输入有效定位后自动使用网格中心经纬度。4 位范围较大，建议使用 6 或 8 位；不改变海拔。</p>
              <p v-if="gridError" class="alert error" role="alert">{{ gridError }}</p>
              <label>纬度<input @input="manualCoordinates" v-model.number="station.latitude" type="number" min="-90" max="90" step="any" required></label>
              <label>经度<input @input="manualCoordinates" v-model.number="station.longitude" type="number" min="-180" max="180" step="any" required></label>
              <label>海拔（米）<input v-model.number="station.altitude_m" type="number" min="-500" max="10000" step="any" required></label>
              <label>显示时区<input v-model="station.timezone" placeholder="Asia/Shanghai" required></label>
              <button class="primary" :disabled="busy || !!gridError">保存观测站</button>
            </form>

            <form class="panel" @submit.prevent="action(async () => { await api('/me/bark', 'PUT', { key: barkKey }); setUser(await api('/me')); barkKey = '' }, 'Bark 设置已保存')">
              <h2>Bark 推送</h2>
              <span class="badge">{{ user.bark_configured ? '已配置' : '未配置' }}</span>
              <p class="muted">输入 Bark App 中的设备 Key。保存后不再显示明文；留空保存可移除。</p>
              <label>设备 Key<input v-model="barkKey" type="password" autocomplete="off" maxlength="256"></label>
              <button class="primary" :disabled="busy">保存 Key</button>
              <button type="button" :disabled="busy || !user.bark_configured" @click="action(() => api('/me/bark/test', 'POST'), '测试通知已提交 Bark')">发送测试通知</button>
            </form>
          </div>
        </section>

        <!-- 4. 通知记录 -->
        <section v-if="page === 'logs'">
          <div class="eyebrow">DELIVERY HISTORY</div>
          <h1>通知记录</h1>
          <p class="muted">保留最近 30 天记录，最多显示 100 条。unknown 表示进程中断后结果不确定，不自动重发。</p>
          <div class="panel empty" v-if="!logs.length">暂无发送记录。配置 Bark 并启用订阅后，后台会自动安排提醒。</div>
          <div class="table-wrap" v-else>
            <table>
              <thead>
                <tr><th>通知</th><th>计划发送</th><th>状态</th><th>尝试次数</th><th>说明</th></tr>
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
          <h1>系统管理</h1>
          <p class="muted">后台调度：{{ worker?.worker_enabled ? '已启用' : '已关闭' }} · 最近运行 {{ time(worker?.last_tick_utc) }}</p>
          <div class="columns">
            <div class="panel">
              <h2>添加 / 刷新卫星</h2>
              <form @submit.prevent="fetchOrbit">
                <label>NORAD 编号<input v-model.number="norad" type="number" min="1" required></label>
                <label>AMSAT 精确名称（可选）<input v-model="amsatName" placeholder="ISS_[FM]"></label>
                <button class="primary" :disabled="busy">从 CelesTrak 获取</button>
              </form>
              <details>
                <summary>手动导入 TLE / OMM</summary>
                <form @submit.prevent="importOrbit">
                  <label>格式
                    <select v-model="orbitFormat">
                      <option value="tle">TLE</option>
                      <option value="omm">OMM JSON（单个对象）</option>
                    </select>
                  </label>
                  <label>轨道数据<textarea v-model="orbitText" rows="6" required></textarea></label>
                  <button :disabled="busy">导入轨道</button>
                </form>
              </details>
            </div>

            <div class="panel">
              <h2>注册管理</h2>
              <label>注册方式
                <select v-model="registerMode">
                  <option value="closed">关闭注册</option>
                  <option value="invite">邀请码注册</option>
                  <option value="approval">管理员审核</option>
                </select>
              </label>
              <button :disabled="busy" @click="action(() => api('/admin/registration', 'PUT', { mode: registerMode }), '注册方式已保存')">保存方式</button>
              <button :disabled="busy" @click="action(async () => { inviteCode = (await api('/admin/invites', 'POST')).invite })">生成邀请码</button>
              <p v-if="inviteCode" class="code">{{ inviteCode }}<br><small>单次使用，7 天有效</small></p>
              <h3>用户审核</h3>
              <div v-for="u in users" :key="u.id" class="user-row">
                <span>{{ u.username }} · {{ u.is_admin ? '管理员' : u.approved ? '已批准' : '待审核 / 停用' }}</span>
                <button v-if="!u.is_admin" :disabled="busy" @click="action(async () => { await api('/admin/users/' + u.id + '/approval', 'PUT', { approved: !u.approved }); await refresh() })">{{ u.approved ? '停用' : '批准' }}</button>
              </div>
            </div>
          </div>
        </section>
      </template>

      <footer>SPA · SAT PASS ALERT <span>开源卫星通联助手</span></footer>
    </main>
  </div>
</template>