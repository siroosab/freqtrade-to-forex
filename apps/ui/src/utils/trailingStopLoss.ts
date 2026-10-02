export function convertTrailingStopPipsToDistance(
  pipsText: string,
  pipSize: number,
  displayPrecision: number,
): string | null {
  if (!pipsText.trim()) return null
  const pips = Number(pipsText)
  if (
    !Number.isFinite(pips) ||
    pips <= 0 ||
    !Number.isFinite(pipSize) ||
    pipSize <= 0 ||
    !Number.isInteger(displayPrecision) ||
    displayPrecision < 0 ||
    displayPrecision > 100
  ) {
    return null
  }
  const distance = pips * pipSize
  if (!Number.isFinite(distance) || distance <= 0) return null
  const formattedDistance = distance.toFixed(displayPrecision)
  return Number(formattedDistance) > 0 ? formattedDistance : null
}

export function validateTrailingStopInput(
  stopLossPrice: string,
  trailingStopPips: string,
  pipSize: number,
  displayPrecision: number,
): string | null {
  if (stopLossPrice.trim() && trailingStopPips.trim()) {
    return 'Use either a fixed stop loss price or a trailing stop loss, not both.'
  }
  if (!trailingStopPips.trim()) return null
  if (!Number.isFinite(Number(trailingStopPips)) || Number(trailingStopPips) <= 0) {
    return 'Trailing stop loss distance must be greater than zero pips.'
  }
  if (
    !Number.isFinite(pipSize) ||
    pipSize <= 0 ||
    !Number.isInteger(displayPrecision) ||
    displayPrecision < 0 ||
    displayPrecision > 100
  ) {
    return 'Live instrument pip size is unavailable; trailing stop loss cannot be converted.'
  }
  if (
    convertTrailingStopPipsToDistance(
      trailingStopPips,
      pipSize,
      displayPrecision,
    ) === null
  ) {
    return 'Trailing stop loss distance is too small for the instrument precision.'
  }
  return null
}
