import { useEffect } from 'react'
import { buildSocketUrl } from '../api/mockApi'
import { useUiStore } from '../store/useUiStore'

const socketTargets = ['/ws/market', '/ws/account', '/ws/orders', '/ws/alerts']

export function useForexSocket() {
  const setConnectionState = useUiStore((state) => state.setConnectionState)
  const setMarketFeed = useUiStore((state) => state.setMarketFeed)
  const setAccountFeed = useUiStore((state) => state.setAccountFeed)
  const setOrdersFeed = useUiStore((state) => state.setOrdersFeed)
  const addAlert = useUiStore((state) => state.addAlert)

  useEffect(() => {
    const sockets: WebSocket[] = []
    const reconnectTimers: number[] = []

    const connect = (path: string) => {
      const socket = new WebSocket(buildSocketUrl(path))
      sockets.push(socket)

      socket.onopen = () => {
        setConnectionState('online')
      }

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (!payload || typeof payload !== 'object') {
            return
          }

          setConnectionState('online')

          const channel = payload.channel ?? payload.type?.split('.')?.[0] ?? null
          const data = payload.data ?? payload

          if (channel === 'market' && data && typeof data === 'object' && 'instruments' in data) {
            setMarketFeed(data)
          }

          if (channel === 'account' && data && typeof data === 'object' && 'equity' in data) {
            setAccountFeed(data)
          }

          if ((channel === 'orders' || channel === 'positions') && Array.isArray(data)) {
            setOrdersFeed(data)
          }

          if (channel === 'alerts' && Array.isArray(data)) {
            const existing = useUiStore.getState().marketFeed
            if (existing) {
              setMarketFeed({ ...existing, alerts: data })
            }

            for (const alert of data) {
              if (alert && typeof alert === 'object' && 'title' in alert && 'detail' in alert) {
                addAlert({
                  title: String(alert.title),
                  detail: String(alert.detail),
                  level: 'warning',
                })
              }
            }
          }

          if (
            ['market.snapshot', 'market.updated', 'market.tick'].includes(payload.type) &&
            data && typeof data === 'object' && 'instruments' in data
          ) {
            setMarketFeed(data)
          }

          if (
            ['account.snapshot', 'account.updated'].includes(payload.type) &&
            data && typeof data === 'object' && 'equity' in data
          ) {
            setAccountFeed(data)
          }

          if (
            ['orders.snapshot', 'orders.updated', 'positions.updated'].includes(payload.type) &&
            Array.isArray(data)
          ) {
            setOrdersFeed(data)
          }

          if (['alerts.snapshot', 'risk.alert'].includes(payload.type) && Array.isArray(data)) {
            const existing = useUiStore.getState().marketFeed
            if (existing) {
              setMarketFeed({ ...existing, alerts: data })
            }

            for (const alert of data) {
              if (alert && typeof alert === 'object' && 'title' in alert && 'detail' in alert) {
                addAlert({
                  title: String(alert.title),
                  detail: String(alert.detail),
                  level: 'warning',
                })
              }
            }
          }
        } catch {
          setConnectionState('online')
        }
      }

      socket.onerror = () => {
        setConnectionState('reconnecting')
      }

      socket.onclose = () => {
        setConnectionState('offline')
        const timer = window.setTimeout(() => {
          connect(path)
        }, 3000)
        reconnectTimers.push(timer)
      }
    }

    socketTargets.forEach(connect)

    return () => {
      reconnectTimers.forEach((timer) => window.clearTimeout(timer))
      sockets.forEach((socket) => socket.close())
    }
  }, [setAccountFeed, setConnectionState, setMarketFeed, setOrdersFeed])
}
