import { test } from 'node:test'
import assert from 'node:assert/strict'
import { setCurrentLang, t, translateAmsat } from './src/i18n.js'

test('Chinese, English and Japanese switch both labels and AMSAT report ages', () => {
  for (const [locale, heading, age] of [
    ['zh-CN', '过境预报', 'Sat/Mode Active（2小时前报告）'],
    ['en', 'Pass Forecast', 'Sat/Mode Active (2h ago)'],
    ['ja', 'パス予報', 'Sat/Mode Active（2時間前に報告）']
  ]) {
    setCurrentLang(locale)
    assert.equal(t('nav_passes'), heading)
    assert.equal(translateAmsat('Sat/Mode Active（2小时前报告）'), age)
  }
  setCurrentLang('zh-CN')
})
