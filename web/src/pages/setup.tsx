import { useEffect, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { $api, type Presets, type SetupState } from '@/api/client'
import { partName, useEngineActions } from '@/api/status'
import { LoadError, useTitle } from '@/components/page'
import { Presence } from '@/components/presence'
import { SectionForm } from '@/components/section-form'
import { runsHere, SlotCard } from '@/components/slot-card'
import { StatusLine } from '@/components/status-line'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useConfirm } from '@/hooks/use-confirm'
import { amountOf, before, useInstall } from '@/hooks/use-install'
import { useSecretStatus } from '@/hooks/use-secrets'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { capitalize, joinNames } from '@/lib/format'
import type { Kind } from '@/lib/presets'
import { SETUP_STEPS } from '@/lib/setup'
import { HomeAssistantSections } from '@/pages/home-assistant'
import { cn } from '@/lib/utils'

const setupKey = ['get', '/api/setup']
const DONE = 'done'
const LLM = ['llm']
// As on the Brain page: thinking and sampling have their own cards there, and keep their defaults here.
const OWN_CARDS = ['thinking', 'sampling']
const CONTROL = ['control']
const KINDS: Kind[] = ['llm', 'tts', 'stt']
const LOCAL = ['127.0.0.1', 'localhost', '::1']

const titleOf = (id: string) => SETUP_STEPS[id]?.title ?? 'Done'

/** The first run (docs/setup.md): his presence and the steps, each done, skipped or still to come, then
 * the step. Opened from the setup link on another device, or on this computer without it. A step can be
 * skipped and picked up later from Set up next on Status. */
export default function SetupPage() {
  const { step } = useParams()
  const { search, pathname } = useLocation()
  const navigate = useNavigate()
  const client = useQueryClient()
  const setup = $api.useQuery('get', '/api/setup')
  const s = useSettings()
  const presets = $api.useQuery('get', '/api/presets')
  const name = $api.useQuery('get', '/api/status').data?.assistant
  const mark = $api.useMutation('put', '/api/setup/steps/{step}', { onSuccess: (data) => client.setQueryData(setupKey, data) })
  useTitle('Set up')
  // The link's key has set its cookie by now: out of the address bar, so it is not bookmarked or passed on.
  useEffect(() => {
    if (new URLSearchParams(search).has('key')) void navigate(pathname, { replace: true })
  }, [search, pathname, navigate])

  if (setup.isError) {
    return (
      <Frame name={name}>
        <LoadError error={setup.error} retrying={setup.isFetching} onRetry={() => void setup.refetch()} />
      </Frame>
    )
  }
  if (!setup.data || s.loading || !presets.data) {
    return (
      <Frame name={name}>
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-40 w-full" />
      </Frame>
    )
  }
  const state = setup.data
  const order = [...state.order, DONE]
  if (!step || !order.includes(step)) return <Navigate to={`/setup/${state.order.find((id) => !state.steps[id]) ?? DONE}`} replace />

  // Done or skipped, on to the next step; a step picked up after setup ended goes back to Status.
  const next = async (how: 'done' | 'skipped') => {
    await mark.mutateAsync({ params: { path: { step } }, body: { state: how } })
    void navigate(state.active ? `/setup/${order[order.indexOf(step) + 1]}` : '/status')
  }
  return (
    <Frame name={name} state={state} order={order} current={step}>
      <p className="text-muted-foreground">{step === DONE ? 'Who can open this page from now on.' : SETUP_STEPS[step].intro}</p>
      {step === 'brain' ? <BrainStep page={s.page} presets={presets.data} next={next} /> : null}
      {step === 'home_assistant' ? <HomeAssistantStep page={s.page} next={next} /> : null}
      {step === DONE ? <DoneStep page={s.page} state={state} name={name} /> : null}
    </Frame>
  )
}

type FrameProps = { name?: string; state?: SetupState; order?: string[]; current?: string; children: ReactNode }

/** His presence and "Set up <name>", the step it is on, the steps with each one's mark, then the step. */
function Frame({ name, state, order = [], current, children }: FrameProps) {
  const at = current ? order.indexOf(current) : -1
  return (
    <div className="flex min-h-svh flex-col items-center">
      <header className="flex w-full max-w-3xl items-center gap-3 px-4 pt-8 pb-6 sm:gap-4">
        <Presence state="idle" className="size-11 shrink-0 sm:size-13" />
        <div className="flex min-w-0 flex-col">
          <h1 className="text-xl font-bold break-words">{name ? `Set up ${name}` : 'Set up'}</h1>
          {current ? (
            <span className="text-muted-foreground max-sm:text-sm">{`Step ${at + 1} of ${order.length}: ${titleOf(current)}`}</span>
          ) : null}
        </div>
      </header>
      <main className="flex w-full max-w-3xl flex-col gap-6 px-4 pb-10">
        {order.length ? (
          <nav aria-label="Setup steps">
            <ol className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
              {order.map((id, i) => {
                const done = state?.steps[id]
                return (
                  <li key={id}>
                    <Link
                      to={`/setup/${id}`}
                      aria-current={id === current ? 'step' : undefined}
                      className={cn('flex items-center gap-2', id === current ? 'font-medium text-foreground' : 'text-muted-foreground hover:text-foreground')}
                    >
                      <span
                        className={cn(
                          'flex size-6 shrink-0 items-center justify-center rounded-full border text-xs',
                          id === current && 'border-primary bg-primary text-primary-foreground',
                        )}
                      >
                        {done === 'done' ? <Check className="size-3.5" aria-label="Done" /> : i + 1}
                      </span>
                      {titleOf(id)}
                      {done === 'skipped' ? <span className="font-normal text-muted-foreground">(skipped)</span> : null}
                    </Link>
                  </li>
                )
              })}
            </ol>
          </nav>
        ) : null}
        {children}
      </main>
    </div>
  )
}

type Actions = { label?: string; busy: boolean; onClick?: () => void; onSkip?: () => void }

/** Skip for now, then the step's own button, when it has one yet. */
function StepActions({ label, busy, onClick, onSkip }: Actions) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-3 border-t pt-4">
      {onSkip ? (
        <Button variant="ghost" disabled={busy} onClick={onSkip}>
          Skip for now
        </Button>
      ) : null}
      {label ? (
        <Button disabled={busy} onClick={onClick}>
          {label}
        </Button>
      ) : null}
    </div>
  )
}

/** Run one of the step's actions, the buttons off meanwhile. */
function useBusy(): [boolean, (run: () => Promise<void>) => Promise<void>] {
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

/** The Home Assistant page's own cards: Connect, with those found on the network and Sign in, until it is
 * connected, then the connection and the devices he can use. Its button saves the list (which applies it
 * there) and goes on; until connected there is only Skip for now. */
function HomeAssistantStep({ page, next }: Pick<StepProps, 'page' | 'next'>) {
  const config = $api.useQuery('get', '/api/config')
  const keys = useSecretStatus()
  const [busy, run] = useBusy()
  const saved = config.data?.providers?.home_assistant
  const connected = !!saved?.url && keys.byName.get(saved.token_env || 'HA_TOKEN')?.set === true
  const go = () =>
    run(async () => {
      if (await page.save()) await next('done')
    })
  return (
    <>
      <HomeAssistantSections page={page} back="/setup/home_assistant" />
      <StepActions
        label={connected ? (page.dirty ? 'Save and continue' : 'Continue') : undefined}
        busy={busy || page.saving}
        onClick={() => void go()}
        onSkip={() => void run(() => next('skipped'))}
      />
    </>
  )
}

type StepProps = { page: SettingsState; presets: Presets; next: (how: 'done' | 'skipped') => Promise<void> }

/** The profile's picks as on the Brain and Voice pages, each with what it still needs downloaded. Until
 * this step is done winstond starts none of them, since a start downloads what it lacks. Its button saves a
 * changed pick and starts them all: each downloads what it needs, with its progress on its card and on
 * Status, then runs, while the next steps go on. */
function BrainStep({ page, presets, next }: StepProps) {
  const ask = useConfirm()
  const { startAll } = useEngineActions()
  const [busy, run] = useBusy()
  const installs = { llm: useInstall(page, 'llm'), tts: useInstall(page, 'tts'), stt: useInstall(page, 'stt') }
  // What still has to start downloading: a part already on its way goes on by itself.
  const waiting = KINDS.map((k) => ({ kind: k, parts: installs[k].missing.filter((p) => p.state !== 'running') })).filter((w) => w.parts.length)
  const go = () =>
    run(async () => {
      if (!(await page.save())) return
      if (waiting.length) {
        const amount = amountOf(waiting.flatMap((w) => w.parts))
        const choice = await ask({
          title: 'Download what he needs?',
          lines: [
            ...(amount ? [`${capitalize(amount.trim())} in all.`] : []),
            ...waiting.map((w) => `${partName(w.kind)}: ${joinNames(w.parts.map(before))}.`),
            'Each part starts once it is here, and you can set up the rest meanwhile.',
          ],
          choices: [{ label: 'Download', value: 'yes' }],
        })
        if (!choice) return
      }
      // The supervisor downloads what each one lacks before it starts it (Installing on Status).
      await startAll.mutateAsync({})
      await next('done')
    })
  return (
    <>
      <SlotCard page={page} kind="llm" title="What runs his brain" presets={presets.llm} quietWhenStopped>
        <SectionForm page={page} path={LLM} omit={[...OWN_CARDS, ...runsHere('llm', page.value)]} />
      </SlotCard>
      <SlotCard page={page} kind="tts" title="Speaking" presets={presets.tts} quietWhenStopped />
      <SlotCard page={page} kind="stt" title="Hearing" presets={presets.stt} quietWhenStopped />
      <StepActions label={waiting.length ? 'Download and continue' : 'Continue'} busy={busy || page.saving} onClick={() => void go()} onSkip={() => void run(() => next('skipped'))} />
    </>
  )
}

/** Who can open this page once setup is done, what was skipped, and Finish, which ends setup mode. From
 * another device that this computer alone may open afterwards, it says so before and where to go after. */
function DoneStep({ page, state, name }: { page: SettingsState; state: SetupState; name?: string }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const finish = $api.useMutation('post', '/api/setup/finish', { onSuccess: (data) => client.setQueryData(setupKey, data) })
  const [busy, run] = useBusy()
  const [closed, setClosed] = useState(false)
  const host = page.value.control?.host?.trim() ?? '127.0.0.1'
  const haSetUp = !!page.value.providers?.home_assistant?.url
  const closing = state.remote && LOCAL.includes(host)
  const him = name ?? 'he'
  const skipped = state.order.filter((id) => state.steps[id] === 'skipped')
  const go = () =>
    run(async () => {
      if (!(await page.save())) return
      await finish.mutateAsync({})
      if (closing) setClosed(true)
      else void navigate('/')
    })
  if (closed) return <p>Setup is done. From now on, open this page on the computer {him} runs on.</p>
  return (
    <>
      {/* As on Status, but it applies at Finish: winstond already listens on the network for setup. */}
      <SectionForm page={page} path={CONTROL} title="Web address">
        {!LOCAL.includes(host) ? (
          <div className="flex flex-col items-start gap-2">
            <StatusLine tone="warning">Anyone on your network can control {name ?? 'him'}{haSetUp ? ' and your home' : ''}.</StatusLine>
            <p className="text-sm text-muted-foreground">While it is open to your network, keys cannot be pasted on these pages.</p>
          </div>
        ) : null}
      </SectionForm>
      {closing ? (
        <StatusLine tone="warning">You are on another device: once you finish, only the computer {him} runs on can open this page.</StatusLine>
      ) : null}
      {skipped.map((id) => (
        <StatusLine key={id} tone="muted">
          {`${SETUP_STEPS[id].title} was skipped. It waits under Set up next on Status.`}
        </StatusLine>
      ))}
      <StepActions label="Finish" busy={busy || page.saving} onClick={() => void go()} />
    </>
  )
}
