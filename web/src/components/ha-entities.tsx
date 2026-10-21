import { KindIcon } from '@/components/kind-icon'
import { RowMenu } from '@/components/row-menu'
import { DataTable, type Column } from '@/components/templates/list-page'
import { Badge } from '@/components/ui/badge'
import { useRegion } from '@/hooks/use-region'
import { haName, heardName, otherNames, type EntityRow, type Group, type HAEntity } from '@/hooks/use-home-assistant'

function Names({ own, ha }: { own: string[]; ha: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {own.map((a) => (
        <Badge key={a} variant="secondary">
          {a}
        </Badge>
      ))}
      {ha.map((a) => (
        <Badge key={a} variant="outline">
          {a}
        </Badge>
      ))}
    </div>
  )
}

function State({ ha }: { ha: HAEntity }) {
  const { date } = useRegion()
  if (ha.state === 'unavailable') return <span className="text-destructive">Unavailable</span>
  if (ha.last_run) return <span className="text-muted-foreground">Last run {date(new Date(ha.last_run))}</span>
  return <span className="text-muted-foreground">{ha.state_text ?? ha.state}</span>
}

type Props = {
  groups: Group[]
  onEdit: (row: EntityRow) => void
  onRemove: (row: EntityRow) => void
}

/** One table per room: the name he hears over Home Assistant's own, the other names (the list's solid,
 * Home Assistant's outlined), the state, and a word only when Home Assistant does not have it as listed. */
export function HAEntityTables({ groups, onEdit, onRemove }: Props) {
  const columns: Column<EntityRow>[] = [
    {
      key: 'name',
      title: 'Name',
      // Every room's table is its own, so the widths are fixed to line their columns up: State fits its words,
      // the other names take the rest. On a phone the lines under the name start where it does, past its icon.
      className: 'w-9/20 whitespace-normal',
      render: (r) => {
        const muted = haName(r)
        return (
          <div className="flex items-start gap-2">
            <KindIcon name={r.id.split('.')[0]} className="mt-0.5 text-muted-foreground" />
            <div className="flex min-w-0 flex-col items-start gap-0.5">
              <button type="button" className="text-left font-medium break-words hover:underline focus-visible:underline" onClick={() => onEdit(r)}>
                {heardName(r)}
              </button>
              {muted ? <span className="text-sm break-words text-muted-foreground">{muted}</span> : null}
              {r.badge ? (
                <Badge variant={r.badge === 'Not in Home Assistant' ? 'destructive' : 'outline'}>{r.badge}</Badge>
              ) : null}
            </div>
          </div>
        )
      },
    },
    {
      key: 'names',
      title: 'Names he answers to',
      className: 'whitespace-normal max-sm:pl-6',
      render: (r) => {
        // An empty cell is null, so its phone label hides with it.
        const { own, ha } = otherNames(r)
        return own.length + ha.length > 0 ? <Names own={own} ha={ha} /> : null
      },
    },
    {
      key: 'state',
      title: 'State',
      className: 'w-28 whitespace-normal max-sm:pl-6',
      render: (r) => (r.ha?.in_ha ? <State ha={r.ha} /> : null),
    },
  ]
  return (
    <div className="flex flex-col gap-4">
      {groups.map((g) => (
        <div key={g.name} role="group" aria-label={g.name} className="flex flex-col gap-1">
          <h3 className="text-sm font-semibold">{g.name}</h3>
          <DataTable
            columns={columns}
            rows={g.rows}
            rowKey={(r) => r.id}
            actions={(r) => (
              <RowMenu
                name={heardName(r)}
                items={[
                  { label: 'Edit', onSelect: () => onEdit(r) },
                  { label: 'Remove', onSelect: () => onRemove(r), destructive: true },
                ]}
              />
            )}
          />
        </div>
      ))}
    </div>
  )
}
