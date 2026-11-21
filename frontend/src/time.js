export function utc(value) {
  if (!value) return null
  return new Date(/[zZ]$|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z')
}
export function displayTime(value, zone = 'Asia/Shanghai') {
  const date = utc(value)
  return date ? new Intl.DateTimeFormat('zh-CN', {timeZone: zone, month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(date) : '—'
}
