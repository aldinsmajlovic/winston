import { useEffect, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { useErrorsUnder } from '@/api/field-errors'
import { $api, fetchClient, type EngineStatus, type Presets, type SetupState } from '@/api/client'
import { pendingRestarts, useEngineActions, useStatus } from '@/api/status'
import { ClonedVoices } from '@/components/cloned-voices'
import { LoadError, useTitle } from '@/components/page'
import { Presence } from '@/components/presence'
import { MemoryBar } from '@/components/memory-bar'
import { SectionForm } from '@/components/section-form'
import { runsHere, SlotCard } from '@/components/slot-card'
import { StatusLine } from '@/components/status-line'
import { Fold } from '@/components/fold'
import { KindIcon } from '@/components/kind-icon'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { amountOf, useInstall } from '@/hooks/use-install'
import { useSecretStatus } from '@/hooks/use-secrets'
import { useSettings, type SettingsState } from '@/hooks/use-settings'
import { useVoicePreviews } from '@/hooks/use-voice-previews'
import { capitalize, size } from '@/lib/format'
import { matchPreset, type Kind } from '@/lib/presets'
import { allBut, setIn } from '@/lib/schema'
import { SETUP_STEPS } from '@/lib/setup'
import { HomeAssistantSections } from '@/pages/home-assistant'
import { FromHomeAssistantCard } from '@/pages/ways-to-talk'
import { PersonalityDocument } from '@/pages/personality'
import { StepActions } from '@/components/step-actions'
import { useBusy } from '@/hooks/use-busy'
import { BoxStep } from '@/pages/setup-box'
import { cn } from '@/lib/utils'

const setupKey = ['get', '/api/setup']
const DONE = 'done'
const LLM = ['llm']
// As on the Brain page: thinking and sampling have their own cards there, and keep their defaults here.
const OWN_CARDS = ['thinking', 'sampling']
const CONTROL = ['control']
const ASSISTANT = ['assistant']
const TTS = ['tts']
const KINDS: Kind[] = ['llm', 'tts', 'stt']
const LOCAL = ['127.0.0.1', 'localhost', '::1']

const titleOf = (id: string) => SETUP_STEPS[id]?.title ?? 'Who can open his pages'

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
      <p className="text-muted-foreground">{step === DONE ? 'Last, who can open his pages from now on. Finish ends setup and takes you to Talk, where you talk to him.' : SETUP_STEPS[step].intro}</p>
      {step === 'brain' ? <BrainStep page={s.page} presets={presets.data} next={next} /> : null}
      {step === 'home_assistant' ? <HomeAssistantStep page={s.page} next={next} /> : null}
      {step === 'who' ? <WhoStep page={s.page} next={next} /> : null}
      {step === 'box' ? <BoxStep page={s.page} next={next} /> : null}
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

/** The Home Assistant page's own cards: Connect, with those found on the network and Sign in, until it is
 * connected, then the connection, the devices he can use, and the two ways Home Assistant talks to him from
 * Ways to talk: Winston's integration and Assist. Its button saves the list (which applies it there) and goes
 * on; until connected there is only Skip for now. */
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
      {connected ? <FromHomeAssistantCard page={page} /> : null}
      <StepActions
        label={connected ? (page.dirty ? 'Save and continue' : 'Continue') : undefined}
        busy={busy || page.saving}
        onClick={() => void go()}
        onSkip={() => void run(() => next('skipped'))}
      />
    </>
  )
}

export type StepProps = { page: SettingsState; presets: Presets; next: (how: 'done' | 'skipped') => Promise<void> }

const PICKS: { kind: Kind; title: string }[] = [
  { kind: 'llm', title: 'What runs his brain' },
  { kind: 'tts', title: 'Speaking' },
  { kind: 'stt', title: 'Hearing' },
]

/** The profile's picks in one card, each with what it still downloads and Change, which opens its card from
 * the Brain and Voice pages under it; then the download in all against the free space, and the memory bar.
 * Until this step is done winstond starts none of them, since a start downloads what it lacks. Its button
 * saves a changed pick, then starts the engines, which download what they lack while the next steps go on. */
function BrainStep({ page, presets, next }: StepProps) {
  const { startAll } = useEngineActions()
  const [busy, run] = useBusy()
  const [open, setOpen] = useState<Kind[]>([])
  const installs = { llm: useInstall(page, 'llm'), tts: useInstall(page, 'tts'), stt: useInstall(page, 'stt') }
  // A refused save opens the card that holds the field.
  const errors = {
    llm: useErrorsUnder([['engines', 'llm'], LLM]),
    tts: useErrorsUnder([['engines', 'tts'], TTS]),
    stt: useErrorsUnder([['engines', 'stt'], ['stt']]),
  }
  // What still has to start downloading: a part already on its way goes on by itself.
  const waiting = KINDS.flatMap((k) => installs[k].missing.filter((p) => p.state !== 'running'))
  const plans = KINDS.map((k) => installs[k].plan.data).filter((d) => d !== undefined)
  const free = plans.find((d) => d.parts.some((p) => !p.installed))?.free
  const shorts = plans.map((d) => d.short).filter((x) => !!x)
  const go = () =>
    run(async () => {
      if (!(await page.save())) return
      // The supervisor downloads what each one lacks before it starts it (Installing on Status).
      await startAll.mutateAsync({})
      await next('done')
    })
  const shown = PICKS.filter((p) => open.includes(p.kind) || errors[p.kind])
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Picked for his computer</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <ul className="flex flex-col divide-y">
            {PICKS.map(({ kind, title }) => {
              const preset = presets[kind].find((p) => p.id === matchPreset(kind, page.value, presets[kind]))
              const off = page.value.engines?.[kind]?.enabled === false
              const missing = installs[kind].missing
              const amount = amountOf(missing).trim()
              const expanded = open.includes(kind)
              return (
                <li key={kind} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="text-sm text-muted-foreground">{title}</span>
                    <span className="flex items-center gap-2 font-medium">
                      {preset && !off ? <KindIcon name={preset.icon} /> : null}
                      {off ? 'Off' : preset?.title ?? 'Custom'}
                    </span>
                    {off ? null : missing.some((p) => p.state === 'running') ? (
                      <span className="text-sm text-muted-foreground">Downloading</span>
                    ) : missing.length ? (
                      <span className="text-sm text-muted-foreground">{amount ? `${amount} to download` : 'Downloads what it needs'}</span>
                    ) : null}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    aria-expanded={expanded}
                    aria-label={`${expanded ? 'Done with' : 'Change'} ${title.toLowerCase()}`}
                    onClick={() => setOpen(expanded ? open.filter((k) => k !== kind) : [...open, kind])}
                  >
                    {expanded ? 'Done' : 'Change'}
                  </Button>
                </li>
              )
            })}
          </ul>
          {waiting.length ? (
            <p className="border-t pt-3 text-sm text-muted-foreground">
              {`${capitalize(amountOf(waiting).trim() || 'Some parts')} to download in all${free != null ? `, ${size(free)} free` : ''}. Each part starts once it is here, and you can set up the rest meanwhile.`}
            </p>
          ) : null}
          {shorts.map((x) => (
            <StatusLine key={x} tone="warning">{`Not enough room: ${x}.`}</StatusLine>
          ))}
        </CardContent>
      </Card>
      {shown.map(({ kind, title }) =>
        kind === 'llm' ? (
          <SlotCard key={kind} page={page} kind="llm" title={title} presets={presets.llm} setup>
            <SectionForm page={page} path={LLM} omit={[...OWN_CARDS, ...runsHere('llm', page.value)]} />
          </SlotCard>
        ) : (
          <SlotCard key={kind} page={page} kind={kind} title={title} presets={presets[kind]} setup />
        ),
      )}
      <MemoryBar page={page} presets={presets} recommend />
      <StepActions label={waiting.length ? 'Download and continue' : 'Continue'} busy={busy || page.saving} onClick={() => void go()} onSkip={() => void run(() => next('skipped'))} />
    </>
  )
}

/** Whether speaking can be heard yet, in words: while it installs, with how far it is. A hosted voice has
 * no engine and can always be heard. */
function speakingLine(tts: EngineStatus | undefined, off: boolean): ReactNode {
  if (off) return <StatusLine tone="muted">Speaking is off, so a voice cannot be heard.</StatusLine>
  if (!tts || tts.state === 'healthy') return null
  const words: Partial<Record<EngineStatus['state'], string>> = {
    installing: `Speaking is still installing${tts.detail ? ` (${tts.detail})` : ''}. You can hear a voice once it runs.`,
    starting: 'Speaking is starting. You can hear a voice in a moment.',
  }
  return <StatusLine tone="muted">{words[tts.state] ?? 'Speaking is not running, so a voice cannot be heard yet.'}</StatusLine>
}

/** His name and reply language as on the Assistant page, his personality as on its own page (each edit
 * saved at once), and his voice as on the Voice page, with Hear this voice once speaking runs, and the
 * voices cloned by asking him. Its button saves, renaming him through the same confirm, which offers to
 * change his name in his personality too. */
function WhoStep({ page, next }: Pick<StepProps, 'page' | 'next'>) {
  const previews = useVoicePreviews(page)
  const tts = useStatus().data?.engines.find((e) => e.name === 'tts')
  const [busy, run] = useBusy()
  return (
    <>
      <SectionForm page={page} path={ASSISTANT} title="Who he is" />
      <PersonalityCard />
      <SectionForm page={page} path={TTS} title="His voice" omit={allBut(page.schema, TTS, ['voice'])} adornments={{ voice: previews.voice }}>
        {speakingLine(tts, page.value.engines?.tts?.enabled === false)}
      </SectionForm>
      <ClonedVoices current={page.value.tts?.voice} onUse={(voice) => page.update((c) => setIn(c, ['tts', 'voice'], voice))} />
      <StepActions
        label={page.dirty ? 'Save and continue' : 'Continue'}
        busy={busy || page.saving}
        onClick={() =>
          void run(async () => {
            if (await page.save()) await next('done')
          })
        }
        onSkip={() => void run(() => next('skipped'))}
      />
    </>
  )
}

/** His personality's first rule, and the whole document folded under it: it is long, and most keep it. */
function PersonalityCard() {
  const behaviour = $api.useQuery('get', '/api/persona').data?.behaviour ?? []
  return (
    <Card>
      <CardHeader>
        <CardTitle>His personality</CardTitle>
        <CardDescription>Instructions only you can change. Each edit is saved at once, with Undo.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {behaviour[0] ? <p>{behaviour[0]}</p> : null}
        <Fold title="Read and change it" summary={behaviour.length > 1 ? `${behaviour.length} rules on how he behaves` : undefined}>
          <div className="pt-2">
            <PersonalityDocument />
          </div>
        </Fold>
      </CardContent>
    </Card>
  )
}

/** Who can open this page once setup is done, what was skipped, and Finish, which ends setup mode. From
 * another device that this computer alone may open afterwards, it says so before and where to go after. */
function DoneStep({ page, state, name }: { page: SettingsState; state: SetupState; name?: string }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const finish = $api.useMutation('post', '/api/setup/finish', { onSuccess: (data) => client.setQueryData(setupKey, data) })
  const { restart } = useEngineActions()
  const [busy, run] = useBusy()
  const [closed, setClosed] = useState(false)
  const host = page.value.control?.host?.trim() ?? '127.0.0.1'
  const port = page.value.control?.port ?? 8700
  const haSetUp = !!page.value.providers?.home_assistant?.url
  // Every address of this computer: the one the network knows it by; one address: that one.
  const reach = ['0.0.0.0', '::', ''].includes(host) ? state.address : host
  const closing = state.remote && LOCAL.includes(host)
  const him = name ?? 'he'
  const skipped = state.order.filter((id) => state.steps[id] === 'skipped')
  const go = () =>
    run(async () => {
      if (!(await page.save())) return
      await finish.mutateAsync({})
      // The steps saved without restarting: what runs with older settings restarts now. One still
      // installing reads the saved config when it starts, and a restart would cut its download.
      const { data } = await fetchClient.GET('/api/status')
      for (const e of pendingRestarts(data).running.filter((e) => e.state !== 'installing'))
        restart.mutate({ params: { path: { name: e.name } } })
      if (closing) setClosed(true)
      else void navigate('/')
    })
  if (closed) return <p>Setup is done. From now on, open http://localhost:{port} on the computer {him} runs on.</p>
  return (
    <>
      {/* As on Status, but it applies at Finish: winstond already listens on the network for setup. */}
      <SectionForm page={page} path={CONTROL} title="Web address">
        {!LOCAL.includes(host) ? (
          <div className="flex flex-col items-start gap-2">
            {reach ? (
              <p>
                From a phone or another device here, open <span className="font-medium">{`http://${reach}:${port}`}</span>.
              </p>
            ) : null}
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
      <StepActions label={closing ? 'Finish' : 'Finish and go to Talk'} busy={busy || page.saving} onClick={() => void go()} />
    </>
  )
}
