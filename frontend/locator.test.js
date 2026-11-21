import test from 'node:test'
import assert from 'node:assert/strict'
import {gridCenter} from './src/locator.js'
test('4, 6, 8 character grid centers and normalization',()=>{
 assert.deepEqual(gridCenter('om20'),{locator:'OM20',latitude:30.5,longitude:105})
 assert.deepEqual(gridCenter(' OM20bo '),{locator:'OM20BO',latitude:30.60416667,longitude:104.125})
 assert.deepEqual(gridCenter('OM20bo24'),{locator:'OM20BO24',latitude:30.60208333,longitude:104.10416667})
 assert.deepEqual(gridCenter('AA00'),{locator:'AA00',latitude:-89.5,longitude:-179})
 assert.deepEqual(gridCenter('RR99'),{locator:'RR99',latitude:89.5,longitude:179})
 assert.equal(gridCenter('JJ00AA00').longitude,0.00416667)
})
test('reject malformed, odd lengths and invalid character ranges',()=>{
 for(const value of ['','OM','OM2','OM20B','OM20BO2','OM20BO240','SS00','OM20YY','OM2X','OM 20','１２３４']) assert.throws(()=>gridCenter(value))
})
