import { useQueryClient } from '@tanstack/react-query'
import { $api, type InstallPlan } from '@/api/client'
import type { SettingsState } from '@/hooks/use-settings'
import { size } from '@/lib/format'
import type { Kind } from '@/lib/presets'

export type InstallPart = InstallPlan['parts'][number]
// openapi-react-query keys a query [method, path, init]: this prefix is every draft's plan.
const planKey = ['post', '/api/install/plan']

/** "the model (16.4 GB)" before a download. */
export const before = (p: InstallPart) => (p.size ? `${p.title} (${size(p.size)})` : p.title)

/** "24.2 GB ", "16.4 GB and more " when a part's size cannot be known first, or "". */
export function amountOf(missing: InstallPart[]): string {
  const known = missing.reduce((n, p) => n + (p.size ?? 0), 0)
  return known ? `${size(known)}${missing.some((p) => p.size == null) ? ' and more' : ''} ` : ''
}

/** What a slot's pick in the draft needs on this computer, polled while it downloads, and the download. */
export function useInstall(page: SettingsState, kind: Kind) {
  const client = useQueryClient()
  const block = page.value.engines?.[kind]
  const body = { engine: kind, block: block ?? null, model: kind === 'llm' ? page.value.llm.model : null }
  const plan = $api.useQuery('post', '/api/install/plan', { body }, {
    enabled: !!block && block.enabled !== false,
    retry: false,
    refetchInterval: (q) => (q.state.data?.parts.some((p) => p.state === 'running') ? 2000 : false),
  })
  const download = $api.useMutation('post', '/api/install', { onSettled: () => client.invalidateQueries({ queryKey: planKey }) })
  const missing = (plan.data?.parts ?? []).filter((p) => !p.installed)
  return { body, plan, download, missing }
}
