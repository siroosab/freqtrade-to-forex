export const OANDA_CHART_TIMEFRAMES = [
  'S5', 'S10', 'S15', 'S30',
  'M1', 'M2', 'M4', 'M5', 'M10', 'M15', 'M30',
  'H1', 'H2', 'H3', 'H4', 'H6', 'H8', 'H12',
  'D', 'W', 'M',
] as const

export const CHART_CANDLE_OPTIONS = [
  { multiplier: 1, count: 100 },
  { multiplier: 2, count: 200 },
  { multiplier: 3, count: 350 },
  { multiplier: 4, count: 900 },
] as const

export function chartCandleCount(multiplier: number) {
  return CHART_CANDLE_OPTIONS.find((option) => option.multiplier === multiplier)?.count ?? CHART_CANDLE_OPTIONS[0].count
}