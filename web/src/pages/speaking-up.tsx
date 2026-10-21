import { $api, type Config } from '@/api/client'
import { Fold } from '@/components/fold'
import { KindList } from '@/components/kind-list'
import { PageSkeleton } from '@/components/page'
import { SectionForm } from '@/components/section-form'
import { StatusLine } from '@/components/status-line'
import { SettingsPage } from '@/components/templates/settings-page'
import { ExtensionIcon } from '@/components/tool-line'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useRegion } from '@/hooks/use-region'
import { useSettings } from '@/hooks/use-settings'
import { allBut, getIn } from '@/lib/schema'
import { cn } from '@/lib/utils'
import { canReachPhone, CopyText, kindItems, NudgeList, setKind, useExtensions, useUnmute } from '@/pages/extensions'

const SPEAK_UP = ['speak_up']
const ANNOUNCE = ['announce']

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`

/** Where he reaches you, in one line, from who you are in Home Assistant, your phone and Discord. */
function ReachLine({ config }: { config: Config }) {
  const away = config.speak_up?.phone ? 'your phone' : config.channels?.discord?.enabled ? 'Discord' : null
  if (!away) {
    return (
      <StatusLine tone="warning" action={{ label: 'Set up Discord', to: '/ways-to-talk' }}>
        Away from home he has nowhere to reach you: choose your phone above, or set up Discord.
      </StatusLine>
    )
  }
  if (!config.speak_up?.home) {
    return <StatusLine tone="muted">Without you in Home Assistant he cannot tell when you are away, so he uses the speakers.</StatusLine>
  }
  return <StatusLine tone="muted">{`At home: the speakers. Away: ${away}.`}</StatusLine>
}

/** When he brings something up on his own, for every extension and for automations: quiet hours and how
 * often, how he reaches you, what automations may ask him to say, each extension's messages, and what he
 * brought up lately. */
export default function SpeakingUpPage() {
  const s = useSettings()
  const list = useExtensions(s.page?.schema)
  const nudges = $api.useQuery('get', '/api/nudges', undefined, { refetchInterval: 10_000 })
  const region = useRegion()
  const unmute = useUnmute()
  if (s.loading) return <PageSkeleton title="Speaking up" />
  const { page } = s
  const config = page.value
  const event = config.announce?.ha_event || 'winston_announce'
  const on = (id: string) => getIn(config, ['extensions', id, 'enabled']) === true
  const sources = (list.data ?? []).filter((e) => e.kinds?.length).sort((a, b) => Number(on(b.id)) - Number(on(a.id)) || a.name.localeCompare(b.name))
  const names = Object.fromEntries((list.data ?? []).map((e) => [e.id, e.name]))
  const recent = nudges.data ?? []
  return (
    <SettingsPage
      title="Speaking up"
      intro="When and where he speaks on his own, from reminders to what an automation asks him to say."
      page={page}
    >
      <Card>
        <CardHeader>
          <CardTitle>When he may speak</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <SectionForm page={page} path={ANNOUNCE} omit={allBut(page.schema, ANNOUNCE, ['quiet_hours'])} />
          <SectionForm page={page} path={SPEAK_UP} omit={['home', 'phone']} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Reaching you</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <SectionForm page={page} path={SPEAK_UP} omit={allBut(page.schema, SPEAK_UP, ['home', 'phone'])} />
          {config.channels?.discord?.enabled ? (
            <SectionForm page={page} path={ANNOUNCE} omit={allBut(page.schema, ANNOUNCE, ['discord'])} />
          ) : config.speak_up?.phone ? (
            <StatusLine tone="muted" action={{ label: 'Set up Discord', to: '/ways-to-talk' }}>
              Announcements can also go to Discord once it is set up.
            </StatusLine>
          ) : null}
          <ReachLine config={config} />
        </CardContent>
      </Card>
      <SectionForm page={page} path={ANNOUNCE} title="From automations and apps" omit={['quiet_hours', 'discord']}>
        <div className="flex flex-col gap-1.5 pt-4">
          <span className="text-sm font-medium">In a Home Assistant automation, add this action</span>
          <CopyText text={`event: ${event}\nevent_data:\n  text: "The laundry is done."`} label="the example" />
        </div>
      </SectionForm>
      {sources.length ? (
        <Card>
          <CardHeader>
            <CardTitle>What extensions may bring up</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {sources.map((e) => {
              const items = kindItems(e, config, region)
              const offCount = items.filter((k) => !k.enabled).length
              const summary = on(e.id) ? `${plural(items.length, 'message')}${offCount ? `, ${offCount} off` : ''}` : 'Off'
              return (
                <Fold key={e.id} title={e.name} icon={<ExtensionIcon name={e.icon} />} summary={summary} to={`/extensions/${e.id}`}>
                  <div className={cn(!on(e.id) && 'opacity-60')}>
                    <KindList
                      items={items}
                      phoneAvailable={canReachPhone(config)}
                      onChange={(kind, change) => setKind(page, e, kind, change)}
                      onUnmute={(kind) => unmute(e.id, kind)}
                    />
                  </div>
                </Fold>
              )
            })}
          </CardContent>
        </Card>
      ) : null}
      {recent.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Recently</CardTitle>
          </CardHeader>
          <CardContent>
            <NudgeList nudges={recent} names={names} max={10} />
          </CardContent>
        </Card>
      ) : null}
    </SettingsPage>
  )
}
