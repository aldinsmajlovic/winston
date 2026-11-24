import { useEffect, useState } from 'react'
import { $api, type UpdateState } from './client'

const BUSY = ['installing', 'restarting']

/** Whether Status shows the card near the top: an update to make, one under way, or one that stopped. */
export const updateNeedsYou = (u?: UpdateState) => !!u && !u.clone && ((!!u.latest && u.latest !== u.version) || !!u.stage)

/** The latest release, checked by winstond, polled every 2 s while an update runs; while the restart takes
 * winstond away, it keeps asking until the new version answers, then reloads the page for its new UI. */
export function useUpdate() {
  const [waitingFor, setWaitingFor] = useState<string | null>(null)
  const query = $api.useQuery('get', '/api/update', undefined, {
    refetchInterval: (q) => (waitingFor || BUSY.includes(q.state.data?.stage ?? '') ? 2000 : false),
    retry: waitingFor ? 3 : 1,
    retryDelay: 1000,
  })
  const u = query.data
  // Kept through the restart, when the answers stop for a while.
  if (u?.stage === 'restarting' && u.detail !== waitingFor) setWaitingFor(u.detail)
  else if (waitingFor && u?.stage === 'installed') setWaitingFor(null) // the restart did not happen
  useEffect(() => {
    if (waitingFor && u?.version === waitingFor) window.location.reload()
  }, [u, waitingFor])
  return { query, waitingFor }
}
