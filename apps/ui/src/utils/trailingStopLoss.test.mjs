import assert from 'node:assert/strict'
import test from 'node:test'
import {
  convertTrailingStopPipsToDistance,
  validateTrailingStopInput,
} from './trailingStopLoss.ts'

test('converts pip distance using the live instrument pip size', () => {
  assert.equal(convertTrailingStopPipsToDistance('15', 0.0001, 5), '0.00150')
  assert.equal(convertTrailingStopPipsToDistance('12.5', 0.01, 3), '0.125')
  assert.equal(convertTrailingStopPipsToDistance('0.01', 0.0001, 5), null)
})

test('rejects invalid trailing stop values and fixed-stop conflicts', () => {
  assert.equal(convertTrailingStopPipsToDistance('0', 0.0001, 5), null)
  assert.equal(validateTrailingStopInput('', '0', 0.0001, 5), 'Trailing stop loss distance must be greater than zero pips.')
  assert.equal(
    validateTrailingStopInput('1.0900', '15', 0.0001, 5),
    'Use either a fixed stop loss price or a trailing stop loss, not both.',
  )
  assert.match(
    validateTrailingStopInput('', '15', Number.NaN, 5),
    /Live instrument pip size is unavailable/,
  )
  assert.match(
    validateTrailingStopInput('', '0.01', 0.0001, 5),
    /too small for the instrument precision/,
  )
})
