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
  { multiplier: 5, count: 1250 },
  { multiplier: 6, count: 1500 },
  { multiplier: 7, count: 1750 },
  { multiplier: 8, count: 2000 },
  { multiplier: 9, count: 2250 },
] as const

export function chartCandleCount(multiplier: number) {
  return CHART_CANDLE_OPTIONS.find((option) => option.multiplier === multiplier)?.count ?? CHART_CANDLE_OPTIONS[0].count
}

export function chartTimeframeFromStrategy(timeframe: string | undefined, fallback: string) {
  if (!timeframe) return fallback

  const rawTimeframe = timeframe.trim()
  if (rawTimeframe === '1M') return 'M'

  const normalized = rawTimeframe.toUpperCase()
  const aliases: Record<string, string> = {
    '1D': 'D',
    D1: 'D',
    '1W': 'W',
    W1: 'W',
    '1MO': 'M',
    MN1: 'M',
  }
  const freqtradeTimeframe = normalized.match(/^(\d+)([SMHDW])$/)
  const chartTimeframe = aliases[normalized]
    ?? (freqtradeTimeframe ? `${freqtradeTimeframe[2]}${freqtradeTimeframe[1]}` : normalized)

  return OANDA_CHART_TIMEFRAMES.find((option) => option === chartTimeframe) ?? fallback
}