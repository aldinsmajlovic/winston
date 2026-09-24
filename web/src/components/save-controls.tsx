import { RefreshCw, RotateCcw, Save, Undo2 } from 'lucide-react'
import { $api, type EngineStatus } from '@/api/client'
import { useConfig, useSchema } from '@/api/config'
import { engineLabel, pendingRestarts, restartCost, useEngineActions, useStatus } from '@/api/status'
import { IconButton } from '@/components/icon-button'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useConfirm } from '@/hooks/use-confirm'
import { DOWNLOAD_COST, mustDownload, optionsParams } from '@/hooks/use-options'
import { type JsonSchema } from '@/lib/schema'

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)
const joinNames = (names: string[]) =>
  names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`

/** "Announce", "Home assistant" or a variable name: what only the next winston up applies. */
function controlLabel(name: string, root: JsonSchema | undefined): string {
  if (/^[A-Z0-9_]+$/.test(name)) return name
  // A section's own title only; its model's title is a class name ("AnnounceConfig").
  const title = root?.properties?.[name]?.title
  return typeof title === 'string' ? title : capitalize(name.replace(/_/g, ' '))
}

/** Saved settings that wait for a restart: the changed icon, its tooltip naming each process and its
 * cost, and Restart for the running ones. What only winstond reads waits for the next winston up. */
function PendingRestart() {
  const status = useStatus()
  const schema = useSchema()
  const actions = useEngineActions()
  const ask = useConfirm()
  const config = useConfig()
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
    ...running.map((e) => `${capitalize(engineLabel(e))}: ${cost(e)}`),
    ...stopped.map((e) => `${capitalize(engineLabel(e))}: when it starts`),
    ...(control.length
      ? ['Applies on the next winston up', control.map((c) => controlLabel(c, schema.data)).join(', ')]
      : []),
  ]

  const restart = async () => {
    const one = running.length === 1
    const choice = await ask({
      title: `Restart ${joinNames(running.map(engineLabel))}?`,
      lines: one ? [capitalize(cost(running[0]))] : running.map((e) => `${capitalize(engineLabel(e))}: ${cost(e)}`),
      choices: [{ label: 'Restart', value: 'yes' }],
    })
    if (!choice) return
    for (const e of running) actions.restart.mutate({ params: { path: { name: e.name } } })
  }

  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex" tabIndex={0}>
            <RefreshCw className="size-4 text-warning" aria-label="Saved changes wait for a restart" />
          </span>
        </TooltipTrigger>
        <TooltipContent className="flex-col items-start gap-0.5">
          {lines.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </TooltipContent>
      </Tooltip>
      {running.length ? (
        <Button variant="outline" size="sm" disabled={actions.restart.isPending} onClick={() => void restart()}>
          <RotateCcw data-icon="inline-start" />
          Restart
        </Button>
      ) : null}
    </>
  )
}

type SaveProps = { dirty: boolean; saving: boolean; onSave: () => void; onReset: () => void }

export function SaveControls({ dirty, saving, onSave, onReset }: SaveProps) {
  return (
    <>
      <PendingRestart />
      <IconButton label="Discard changes" disabled={!dirty || saving} onClick={onReset}>
        <Undo2 />
      </IconButton>
      <Button size="sm" disabled={!dirty || saving} onClick={onSave}>
        <Save data-icon="inline-start" />
        Save
      </Button>
    </>
  )
}

type AdvancedProps = { checked: boolean; onChange: (checked: boolean) => void }

export function AdvancedToggle({ checked, onChange }: AdvancedProps) {
  return (
    <div className="flex items-center gap-2">
      <Switch id="advanced" checked={checked} onCheckedChange={onChange} />
      <Label htmlFor="advanced">Advanced</Label>
    </div>
  )
}
