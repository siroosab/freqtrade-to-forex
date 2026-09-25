import { useQuery } from '@tanstack/react-query'
import { getMarketSummary } from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

export function MarketPage() {
  const { data } = useQuery({ queryKey: ['market'], queryFn: getMarketSummary })
  const liveMarket = useUiStore((state) => state.marketFeed)

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">Market</p>
          <h2>Instrument overview</h2>
        </div>
      </header>

      <section className="panel page-panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Liquidity</p>
            <h3>Cross-pair pricing</h3>
          </div>
        </div>

        <div className="watchlist">
          {(liveMarket ?? data)?.instruments.map((item) => (
            <div key={item.pair} className="watch-row">
              <div className="pair-block">
                <span className="pair-name">{item.pair}</span>
                <span className="pair-change positive">{item.change}</span>
              </div>
              <div className="price-block">
                <span>Bid</span>
                <strong>{item.bid}</strong>
              </div>
              <div className="price-block">
                <span>Ask</span>
                <strong>{item.ask}</strong>
              </div>
              <div className="price-block">
                <span>Spread</span>
                <strong>{item.spread}</strong>
              </div>
            </div>
          ))}
        </div>
      </section>
    </>
  )
}
