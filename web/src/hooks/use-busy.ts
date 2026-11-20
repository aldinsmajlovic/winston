import { useState } from 'react'

/** Run one of the step's actions, the buttons off meanwhile. */
export function useBusy(): [boolean, (run: () => Promise<void>) => Promise<void>] {
  const [busy, setBusy] = useState(false)
  return [
    busy,
    async (run) => {
      setBusy(true)
      try {
        await run()
      } finally {
        setBusy(false)
      }
    },
  ]
}
