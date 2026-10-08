import { useEffect, useState } from 'react'

/** The time in ms, read again every `ms`: for "3 min ago" and states that time out. */
export function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(timer)
  }, [ms])
  return now
}
