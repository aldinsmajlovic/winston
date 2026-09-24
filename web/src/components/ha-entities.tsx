import { CircleAlert, CircleCheck, CircleDashed, CircleHelp, Trash2 } from 'lucide-react'
import { StatusTip } from '@/components/ha-connection'
import { IconButton } from '@/components/icon-button'
import { KindIcon } from '@/components/kind-icon'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { aliasesOf, haName, heardName, type EntityRow, type Group, type HAEntity, type RowStatus } from '@/hooks/use-home-assistant'

const SHOWN_ALIASES = 2

function Aliases({ row }: { row: EntityRow }) {
  const all = aliasesOf(row)
  const rest = all.slice(SHOWN_ALIASES)
  return (
    <div className="flex flex-wrap gap-1">
      {all.slice(0, SHOWN_ALIASES).map((a) => (
        <Badge key={a} variant="secondary">
          {a}
        </Badge>
      ))}
      {rest.length ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="outline" tabIndex={0} aria-label={`${rest.length} more aliases`}>
              +{rest.length}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>{rest.join(', ')}</TooltipContent>
        </Tooltip>
      ) : null}
    </div>
  )
}

function State({ ha }: { ha?: HAEntity }) {
  if (!ha?.in_ha) return null
  if (ha.state === 'unavailable') return <span className="text-destructive">Unavailable</span>
  const text = ha.state_text ?? ha.state ?? undefined
  return (
    <span className="text-muted-foreground" title={text}>
      {text}
    </span>
  )
}

export function StatusIcon({ status }: { status: RowStatus }) {
  switch (status.kind) {
    case 'applied':
      return <StatusTip icon={CircleCheck} className="text-success" label="Applied" />
    case 'pending':
      return <StatusTip icon={CircleDashed} className="text-warning" label="Not applied yet" lines={status.why} />
    case 'missing':
      return (
        <StatusTip
          icon={CircleAlert}
          className="text-destructive"
          label="Not in Home Assistant"
          lines={['Home Assistant has no entity with this id; a save skips it']}
        />
      )
    case 'unknown':
      return <StatusTip icon={CircleHelp} className="text-muted-foreground" label="Unknown" lines={[status.why]} />
  }
}

type Props = {
  groups: Group[]
  onEdit: (row: EntityRow) => void
  onRemove: (row: EntityRow) => void
  /** Shown as the one row when there are no groups. */
  empty: string
}

/** One table per room: domain icon, the name he hears over Home Assistant's own, aliases, state and
 * whether Home Assistant has it as the list does. A row opens the edit dialog. */
export function HAEntityTables({ groups, onEdit, onRemove, empty }: Props) {
  const tables = groups.length ? groups : [{ name: 'Entities', rows: [] }]
  return (
    <div className="flex flex-col gap-4">
      {tables.map((g) => (
        <Table key={g.name} aria-label={g.name} className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>{g.name}</TableHead>
              <TableHead className="hidden w-2/5 sm:table-cell">Aliases</TableHead>
              <TableHead className="w-28">State</TableHead>
              <TableHead className="w-8" />
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {g.rows.length === 0 ? (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={6} className="text-muted-foreground">
                  {empty}
                </TableCell>
              </TableRow>
            ) : null}
            {g.rows.map((r) => {
              const muted = haName(r)
              return (
                <TableRow key={r.id} className="cursor-pointer" onClick={() => onEdit(r)}>
                  <TableCell>
                    <KindIcon name={r.id.split('.')[0]} className="text-muted-foreground" />
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    <div className="flex min-w-0 flex-col">
                      <span className="font-medium break-words">{heardName(r)}</span>
                      {muted ? <span className="text-xs break-words text-muted-foreground">{muted}</span> : null}
                    </div>
                  </TableCell>
                  <TableCell className="hidden whitespace-normal sm:table-cell">
                    <Aliases row={r} />
                  </TableCell>
                  <TableCell className="truncate">
                    <State ha={r.ha} />
                  </TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <StatusIcon status={r.status} />
                  </TableCell>
                  <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                    <IconButton label="Remove" onClick={() => onRemove(r)}>
                      <Trash2 />
                    </IconButton>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      ))}
    </div>
  )
}
