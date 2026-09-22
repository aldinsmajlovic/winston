import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { errorText, type Config } from '@/api/client'
import { useConfig, useSaveConfig, useSchema } from '@/api/config'
import { $api } from '@/api/client'
import { useEngineActions } from '@/api/status'
import { useConfirm } from '@/hooks/use-confirm'
import { useDraft } from '@/hooks/use-draft'
import { duration } from '@/lib/format'
import { deepEqual, hasBasicFields, section, type JsonSchema } from '@/lib/schema'

export type SettingsState = {
  schema: JsonSchema
  value: Config
  dirty: boolean
  saving: boolean
  advanced: boolean
  setAdvanced: (advanced: boolean) => void
  update: (fn: (current: Config) => Config) => void
  reset: () => void
  save: () => Promise<void>
}

type Loading = { loading: true; page: null }
type Ready = { loading: false; page: SettingsState }

/** A page's draft of the config plus the save flow: validate, confirm what restarts, save,
 * restart. `paths` are the sections the page shows; a page with no basic field opens Advanced. */
export function useSettings(paths: string[][]): Loading | Ready {
  const config = useConfig()
  const schema = useSchema()
  const saveConfig = useSaveConfig()
  const actions = useEngineActions()
  const status = $api.useQuery('get', '/api/status')
  const ask = useConfirm()
  const { draft, setDraft } = useDraft()
  const [advancedChoice, setAdvancedChoice] = useState<boolean | null>(null)

  const base = config.data
  const update = useCallback(
    (fn: (current: Config) => Config) => setDraft((d) => (d ?? base ? fn((d ?? base) as Config) : d)),
    [base, setDraft],
  )
  const reset = useCallback(() => setDraft(null), [setDraft])

  if (!base || !schema.data) return { loading: true, page: null }
  const root = schema.data
  const value = draft ?? base
  const dirty = draft !== null && !deepEqual(draft, base)
  const anyBasic = paths.some((p) => hasBasicFields(section(root, p), root))
  const advanced = advancedChoice ?? !anyBasic

  const costLine = (name: string): string => {
    const engine = status.data?.engines.find((e) => e.name === name)
    if (!engine) return `${name}: applies on the next winston up`
    return engine.started_in
      ? `${name}: about ${duration(engine.started_in)}`
      : `${name}: up to ${duration(engine.startup_timeout)}`
  }

  const save = async () => {
    if (!dirty) return
    let restarts: string[]
    try {
      restarts = (await saveConfig.mutateAsync({ body: value, params: { query: { dry_run: true } } })).restarts
    } catch (error) {
      toast.error(errorText(error))
      return
    }
    let restart = false
    if (restarts.length) {
      const choice = await ask({
        title: `Save and restart ${restarts.join(', ')}?`,
        lines: restarts.map(costLine),
        choices: [
          { label: 'Save only', value: 'save' },
          { label: 'Save and restart', value: 'restart' },
        ],
      })
      if (!choice) return
      restart = choice === 'restart'
    }
    try {
      await saveConfig.mutateAsync({ body: value })
    } catch (error) {
      toast.error(errorText(error))
      return
    }
    toast.success('Saved')
    setDraft(null)
    if (restart) {
      const managed = new Set(status.data?.engines.map((e) => e.name))
      for (const name of restarts) {
        if (managed.has(name)) actions.restart.mutate({ params: { path: { name } } })
      }
    }
  }

  return {
    loading: false,
    page: {
      schema: root, value, dirty, saving: saveConfig.isPending, advanced, setAdvanced: setAdvancedChoice,
      update, reset, save,
    },
  }
}
