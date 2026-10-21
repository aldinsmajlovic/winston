import { RefreshCw, RotateCcw, Save } from 'lucide-react'
import { $api, type EngineStatus, type SecretStatus } from '@/api/client'
import { useConfig, useSchema } from '@/api/config'
import { engineLabel, partName, pendingRestarts, restartCost, useEngineActions, useStatus } from '@/api/status'
import { IconButton } from '@/components/icon-button'
import { StatusTip } from '@/components/status-tip'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/hooks/use-confirm'
import { DOWNLOAD_COST, mustDownload, optionsParams } from '@/hooks/use-options'
import { useSecretStatus } from '@/hooks/use-secrets'
import { keyLabel, type SettingsState } from '@/hooks/use-settings'
import { capitalize, humanize, joinNames } from '@/lib/format'
import { type JsonSchema } from '@/lib/schema'
import { cn } from '@/lib/utils'

/** What only Winston itself reads (its web address, a variable): applied when it restarts. */
const WINSTON_RESTART = 'Takes effect when Winston restarts'

/** "Announcements", "Home Assistant" or a key by its field ("Access token"): what only Winston's own restart
 * applies. A variable nothing names is shown by its name. */
function controlLabel(name: string, root: JsonSchema | undefined, byName: Map<string, SecretStatus>): string {
  if (/^[A-Z0-9_]+$/.test(name)) {
    const path = byName.get(name)?.used_by?.[0]
    return root && path ? keyLabel(root, path, name) : name
  }
  // A section's own title only; its model's title is a class name ("AnnounceConfig").
  const title = root?.properties?.[name]?.title
  return typeof title === 'string' ? title : humanize(name)
}

/** Saved settings that wait for a restart, in a few words beside Save ("Waiting for a restart: the brain",
 * or "2 parts") with one Restart for the running ones, whose confirm names each part and its cost; the
 * icon opens them all. What only Winston itself reads waits for its own restart. The words shorten as the
 * header narrows; in a phone's width the icon alone stands for it, as the Restart button while something
 * can restart. */
function PendingRestart() {
  const status = useStatus()
  const schema = useSchema()
  const actions = useEngineActions()
  const ask = useConfirm()
  const config = useConfig()
  const { byName } = useSecretStatus()
  const { running, stopped, control } = pendingRestarts(status.data)
  // The saved brain model, when its restart waits: does the restart download it first.
  const brain = running.some((e) => e.name === 'llm') && config.data
  const models = $api.useQuery('get', '/api/options/{name}', optionsParams('llm_models', config.data || undefined), {
    staleTime: 30_000,
    enabled: !!brain,
  })
  const cost = (e: EngineStatus) =>
    e.name === 'llm' && brain && mustDownload(models.data, brain.llm.model) ? DOWNLOAD_COST : restartCost(e)
  if (!running.length && !stopped.length && !control.length) return null

  const lines = [
    ...running.map((e) => `${partName(e.name)}: ${cost(e)}`),
    ...stopped.map((e) => `${partName(e.name)}: when it starts`),
    ...(control.length ? [`${WINSTON_RESTART}: ${control.map((c) => controlLabel(c, schema.data, byName)).join(', ')}`] : []),
  ]
  // "the brain" or "2 parts", so the line stays short however many wait.
  const named = (list: EngineStatus[]) => (list.length === 1 ? engineLabel(list[0]) : `${list.length} parts`)
  const text = running.length
    ? `Waiting for a restart: ${named(running)}`
    : stopped.length
      ? `Waiting for ${named(stopped)} to start`
      : WINSTON_RESTART

  const restart = async () => {
    const one = running.length === 1
    const choice = await ask({
      title: `Restart ${joinNames(running.map(engineLabel))}?`,
      lines: one ? [capitalize(cost(running[0]))] : running.map((e) => `${partName(e.name)}: ${cost(e)}`),
      choices: [{ label: 'Restart', value: 'yes' }],
    })
    if (!choice) return
    for (const e of running) actions.restart.mutate({ params: { path: { name: e.name } } })
  }

  return (
    <>
      <span className={cn('flex', running.length > 0 && '@max-xl/header:hidden')}>
        <StatusTip icon={RefreshCw} className="text-warning" label="Saved changes wait for a restart" lines={lines} />
      </span>
      {/* The room left over, up to its own width: the words shorten to nothing before the title gives up any,
          since even a fraction of a pixel cuts the title to "...". */}
      <span className="min-w-0 max-w-max flex-1 truncate text-sm text-muted-foreground @max-xl/header:hidden">{text}</span>
      {running.length ? (
        <>
          <Button variant="outline" size="sm" className="@max-xl/header:hidden" disabled={actions.restart.isPending} onClick={() => void restart()}>
            <RotateCcw data-icon="inline-start" />
            Restart
          </Button>
          <IconButton label="Restart" variant="outline" className="@xl/header:hidden" disabled={actions.restart.isPending} onClick={() => void restart()}>
            <RefreshCw className="text-warning" />
          </IconButton>
        </>
      ) : null}
    </>
  )
}

const changeCount = (n: number) => (n === 1 ? '1 unsaved change' : `${n} unsaved changes`)

/** Discard and Save in the header. On a phone they move to the SaveBar. */
export function SaveControls({ page }: { page: SettingsState }) {
  return (
    <>
      <PendingRestart />
      {page.dirty ? (
        <Button variant="ghost" size="sm" className="max-sm:hidden" disabled={page.saving} onClick={page.reset}>
          Discard
        </Button>
      ) : null}
      <Button size="sm" className="max-sm:hidden" disabled={!page.dirty || page.saving} onClick={() => void page.save()}>
        <Save data-icon="inline-start" />
        Save
      </Button>
    </>
  )
}

/** On a phone, Discard and Save along the bottom of the screen while there is something to save. */
export function SaveBar({ page }: { page: SettingsState }) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-10 flex items-center gap-2 border-t bg-background px-4 py-3 sm:hidden">
      <span className="mr-auto text-sm text-muted-foreground">{changeCount(page.changes)}</span>
      <Button variant="ghost" size="sm" disabled={page.saving} onClick={page.reset}>
        Discard
      </Button>
      <Button size="sm" disabled={page.saving} onClick={() => void page.save()}>
        <Save data-icon="inline-start" />
        Save
      </Button>
    </div>
  )
}
