import test from 'node:test'
import assert from 'node:assert/strict'
import {utc,displayTime} from './src/time.js'
test('database UTC and explicit offsets are equivalent',()=>{
 assert.equal(utc('2026-11-21T00:00:00').toISOString(),'2026-11-21T00:00:00.000Z')
 assert.equal(utc('2026-11-21T08:00:00+08:00').toISOString(),'2026-11-21T00:00:00.000Z')
 assert.match(displayTime('2026-11-21T00:00:00','Asia/Shanghai'),/08:00:00/)
 assert.equal(displayTime(null),'—')
})
