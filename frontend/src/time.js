import { currentLang } from './i18n.js'
export function utc(value) {
  if (!value) return null
  return new Date(/[zZ]$|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z')
}
export function displayTime(value, zone = 'Asia/Shanghai') {
  const date = utc(value)
  if (!date) return '\u2014'
  const locale = currentLang.value === 'en' ? 'en-GB' : currentLang.value
  return new Intl.DateTimeFormat(locale, {
    timeZone: zone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
  }).format(date)
}
