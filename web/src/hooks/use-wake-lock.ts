import { useEffect } from 'react'

/** Keeps the screen on while the page is visible, for a wall tablet. Browsers only allow it on
 * HTTPS or localhost; elsewhere it quietly does nothing. */
export function useWakeLock() {
  useEffect(() => {
    let lock: WakeLockSentinel | undefined
    const request = () => {
      if (document.visibilityState !== 'visible' || !('wakeLock' in navigator)) return
      navigator.wakeLock.request('screen').then(
        (sentinel) => (lock = sentinel),
        () => undefined,
      )
    }
    request()
    document.addEventListener('visibilitychange', request)
    return () => {
      document.removeEventListener('visibilitychange', request)
      void lock?.release()
    }
  }, [])
}
