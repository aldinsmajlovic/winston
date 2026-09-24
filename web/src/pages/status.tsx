import { Play, RotateCw, Square } from 'lucide-react'
import type { EngineStatus } from '@/api/client'
import { useEngineActions, useStatus } from '@/api/status'
import { IconButton } from '@/components/icon-button'
import { Page, PageSkeleton } from '@/components/page'
import { ChangedIcon, DriverLabel, StateIcon } from '@/components/state-icon'
import { DataTable, type Column } from '@/components/templates/list-page'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { useConfirm } from '@/hooks/use-confirm'
import { useDrivers, type DriverChoice } from '@/hooks/use-drivers'
import { duration, since } from '@/lib/format'

const running = (e: EngineStatus) => e.state !== 'stopped' && e.state !== 'error'
const cost = (e: EngineStatus) =>
  e.started_in ? `about ${duration(e.started_in)}` : `up to ${duration(e.startup_timeout)}`

const columns = (drivers: DriverChoice[]): Column<EngineStatus>[] => [
  { key: 'state', title: '', className: 'w-0', render: (e) => <StateIcon state={e.state} detail={e.detail} /> },
  { key: 'name', title: 'Name', render: (e) => <span className="font-medium">{e.name}</span> },
  { key: 'kind', title: 'Kind', render: (e) => <Badge variant="outline">{e.kind}</Badge> },
  { key: 'driver', title: 'Driver', render: (e) => <DriverLabel driver={e.driver} drivers={drivers} /> },
  { key: 'port', title: 'Port', render: (e) => e.port || null },
  { key: 'since', title: 'For', render: (e) => since(e.since) },
  { key: 'changed', title: '', className: 'w-0', render: (e) => (e.config_changed ? <ChangedIcon /> : null) },
]

/** Template: status page. Icons and colours for state, icon actions, one gauge. */
export default function StatusPage() {
  const status = useStatus()
  const actions = useEngineActions()
  const ask = useConfirm()
  const drivers = useDrivers()
  if (!status.data) return <PageSkeleton title="Status" />
  const { engines, gpu } = status.data
  const byName = (name: string) => ({ params: { path: { name } } })

  const confirmed = async (title: string, lines: string[], label: string, destructive: boolean, run: () => void) => {
    if (await ask({ title, lines, choices: [{ label, value: 'yes', destructive }] })) run()
  }

  return (
    <Page
      title="Status"
      actions={
        <>
          <IconButton label="Start all" variant="outline" size="icon" onClick={() => actions.startAll.mutate({})}>
            <Play />
          </IconButton>
          <IconButton
            label="Stop all"
            variant="outline"
            size="icon"
            onClick={() =>
              confirmed(
                'Stop everything?',
                engines.filter(running).map((e) => `${e.name}: starting again takes ${cost(e)}`),
                'Stop all',
                true,
                () => actions.stopAll.mutate({}),
              )
            }
          >
            <Square />
          </IconButton>
        </>
      }
    >
      <DataTable
        columns={columns(drivers)}
        rows={engines}
        rowKey={(e) => e.name}
        actions={(e) =>
          running(e) ? (
            <>
              <IconButton
                label="Restart"
                onClick={() =>
                  confirmed(`Restart ${e.name}?`, [cost(e)], 'Restart', false, () => actions.restart.mutate(byName(e.name)))
                }
              >
                <RotateCw />
              </IconButton>
              <IconButton
                label="Stop"
                onClick={() =>
                  confirmed(`Stop ${e.name}?`, [`starting again takes ${cost(e)}`], 'Stop', true, () =>
                    actions.stop.mutate(byName(e.name)),
                  )
                }
              >
                <Square />
              </IconButton>
            </>
          ) : (
            <IconButton label="Start" onClick={() => actions.start.mutate(byName(e.name))}>
              <Play />
            </IconButton>
          )
        }
      />
      {gpu ? (
        <Card>
          <CardHeader>
            <CardTitle>GPU</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <Progress value={(gpu.used_mb / gpu.total_mb) * 100} />
            <div className="flex justify-between text-sm text-muted-foreground">
              <span>{gpu.name}</span>
              <span>
                {gpu.used_mb} / {gpu.total_mb} MB
              </span>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </Page>
  )
}
