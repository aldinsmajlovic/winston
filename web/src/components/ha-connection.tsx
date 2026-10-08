import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Blocks, CircleAlert, CircleCheck, CloudUpload, LoaderCircle, RefreshCw, Wifi, WifiOff } from 'lucide-react'
import { $api, errorText } from '@/api/client'
import { IconButton } from '@/components/icon-button'
import { StatusTip } from '@/components/status-tip'
import { refreshHomeAssistant, useApplyNow, type ExposurePlan, type HAEntity, type HAExposure } from '@/hooks/use-home-assistant'

const names = (entities: HAEntity[]) => entities.map((e) => e.friendly_name ?? e.entity_id).join(', ')

/** What a save would still change in Home Assistant, a line per kind. */
function pendingLines(plan: ExposurePlan | null | undefined): string[] {
  if (!plan) return []
  const lines: string[] = []
  if (plan.expose?.length) lines.push(`To expose: ${plan.expose.join(', ')}`)
  if (plan.unexpose?.length) lines.push(`To un-expose: ${plan.unexpose.join(', ')}`)
  const add = Object.entries(plan.aliases_add ?? {})
  if (add.length) lines.push(`Aliases to add: ${add.map(([id, a]) => `${id} (${a.join(', ')})`).join('; ')}`)
  const remove = Object.entries(plan.aliases_remove ?? {})
  if (remove.length) lines.push(`Aliases to remove: ${remove.map(([id, a]) => `${id} (${a.join(', ')})`).join('; ')}`)
  if (plan.skipped?.length) lines.push(`Not in Home Assistant: ${plan.skipped.join(', ')}`)
  if (plan.auto_expose_off) lines.push('New entities are exposed automatically')
  return lines
}

/** Reachable at the draft's URL when it is not the saved one (the rest follows the saved config). */
function DraftReachable({ url }: { url: string }) {
  const check = $api.useQuery('get', '/api/providers/home_assistant/check', { params: { query: { url } } }, { enabled: url !== '' })
  if (!check.data) return <StatusTip icon={LoaderCircle} className="animate-spin text-muted-foreground" label="Checking" />
  return check.data.ok ? (
    <StatusTip icon={Wifi} className="text-success" label="Reachable" lines={[check.data.detail, 'Save to see the rest']} />
  ) : (
    <StatusTip icon={WifiOff} className="text-destructive" label="Not reachable" lines={[check.data.detail]} />
  )
}

type Props = {
  exposure: { data?: HAExposure; error: unknown; isFetching: boolean; isLoading: boolean }
  /** The draft's URL differs from the saved one. */
  draftUrl?: string
  /** Unsaved edits: Apply would apply the saved list, not what the page shows. */
  dirty: boolean
}

/** Beside the Connection title: reachable, MCP server, in sync; Sync reads Home Assistant again, and
 * Apply catches up on a save Home Assistant missed. */
export function HAConnectionStatus({ exposure, draftUrl, dirty }: Props) {
  const client = useQueryClient()
  const apply = useApplyNow()
  const data = exposure.data
  let icons: ReactNode
  if (draftUrl !== undefined) icons = <DraftReachable url={draftUrl} />
  else if (exposure.isLoading) icons = <StatusTip icon={LoaderCircle} className="animate-spin text-muted-foreground" label="Checking" />
  else if (!data) icons = <StatusTip icon={WifiOff} className="text-destructive" label="Not reachable" lines={[errorText(exposure.error)]} />
  else if (!data.ok) icons = <StatusTip icon={WifiOff} className="text-destructive" label="Not reachable" lines={[data.detail]} />
  else {
    const pending = pendingLines(data.pending)
    const drift = data.drift ?? []
    icons = (
      <>
        <StatusTip icon={Wifi} className="text-success" label="Reachable" lines={[`Home Assistant ${data.version ?? ''}`.trim()]} />
        {data.mcp_server === true ? (
          <StatusTip icon={Blocks} className="text-success" label="MCP server loaded" />
        ) : data.mcp_server === false ? (
          <StatusTip
            icon={Blocks}
            className="text-warning"
            label="MCP server not loaded"
            lines={['Add the Model Context Protocol Server integration in Home Assistant; without it he has no tools there']}
          />
        ) : (
          <StatusTip icon={Blocks} className="text-muted-foreground" label="MCP server unknown" lines={[data.detail]} />
        )}
        {!data.registry ? (
          <StatusTip
            icon={CircleAlert}
            className="text-muted-foreground"
            label="Sync unknown"
            lines={['Comparing needs an admin token: the registry and exposure are admin only']}
          />
        ) : data.in_sync ? (
          <StatusTip icon={CircleCheck} className="text-success" label="In sync" />
        ) : (
          <StatusTip
            icon={CircleAlert}
            className="text-warning"
            label="Not in sync"
            lines={[...pending, ...(drift.length ? [`Exposed in Home Assistant, not on the list: ${names(drift)}`] : [])]}
          />
        )}
      </>
    )
  }
  const canApply = draftUrl === undefined && !!data?.ok && data.registry && !data.in_sync
  return (
    <span className="ml-auto flex items-center gap-2">
      <span className="flex items-center gap-2">{icons}</span>
      {canApply ? (
        <IconButton
          label={dirty ? 'Apply the saved list to Home Assistant (not the unsaved edits)' : 'Apply the saved list to Home Assistant'}
          variant="outline"
          disabled={apply.isPending}
          onClick={() => apply.mutate({})}
        >
          {apply.isPending ? <LoaderCircle className="animate-spin" /> : <CloudUpload />}
        </IconButton>
      ) : null}
      <IconButton
        label="Sync"
        variant="outline"
        disabled={draftUrl !== undefined}
        onClick={() => refreshHomeAssistant(client)}
      >
        <RefreshCw className={exposure.isFetching ? 'animate-spin' : undefined} />
      </IconButton>
    </span>
  )
}
