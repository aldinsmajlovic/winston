import type { ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'

export type Column<T> = { key: string; title: string; render: (row: T) => ReactNode; className?: string }

type TableProps<T> = {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  actions?: (row: T) => ReactNode
  /** Where a row leads: its first cell becomes a link that covers the row, with a chevron at its end. */
  rowLink?: (row: T) => string
  /** What an empty table says: what belongs here and how it gets filled. */
  empty?: ReactNode
}

/** Rows of columns; on a phone each row stacks into lines, the first as its title and the others led
 * by their column's title, and an unlinked row's actions sit at its top right. A control in a linked row's column sits above the link with
 * `relative z-10` in its className, as the actions do. */
export function DataTable<T>({ columns, rows, rowKey, actions, rowLink, empty = 'None yet.' }: TableProps<T>) {
  return (
    <Table className="max-sm:block">
      {rows.length > 0 && columns.some((c) => c.title) ? (
        <TableHeader className="max-sm:hidden">
          <TableRow>
            {columns.map((c) => (
              <TableHead key={c.key} className={c.className}>
                {c.title}
              </TableHead>
            ))}
            {actions ? <TableHead className="w-0" /> : null}
            {rowLink ? <TableHead className="w-0" /> : null}
          </TableRow>
        </TableHeader>
      ) : null}
      <TableBody className="max-sm:block">
        {rows.length === 0 ? (
          <TableRow className="hover:bg-transparent max-sm:block">
            <TableCell
              colSpan={columns.length + (actions ? 1 : 0) + (rowLink ? 1 : 0)}
              className="whitespace-normal px-0 text-muted-foreground max-sm:block"
            >
              {empty}
            </TableCell>
          </TableRow>
        ) : null}
        {rows.map((row) => (
          <TableRow
            key={rowKey(row)}
            className={cn(
              'max-sm:flex max-sm:flex-col max-sm:py-2',
              rowLink
                ? 'relative has-[a:focus-visible]:ring-2 has-[a:focus-visible]:ring-ring has-[a:focus-visible]:ring-inset max-sm:pr-8'
                : actions && 'relative max-sm:pr-10',
            )}
          >
            {columns.map((c, i) => {
              const content = c.render(row)
              return (
                <TableCell
                  key={c.key}
                  className={cn(
                    'max-sm:w-auto max-sm:px-0 max-sm:py-0.5 max-sm:whitespace-normal max-sm:empty:hidden',
                    i > 0 && 'max-sm:text-muted-foreground',
                    c.className,
                  )}
                >
                  {i === 0 && rowLink ? (
                    <Link to={rowLink(row)} className="outline-none after:absolute after:inset-0">
                      {content}
                    </Link>
                  ) : (
                    <>
                      {i > 0 && c.title && content != null && content !== '' ? <span className="sm:hidden">{c.title}: </span> : null}
                      {content}
                    </>
                  )}
                </TableCell>
              )
            })}
            {actions ? (
              <TableCell
                className={cn(
                  'relative z-10 text-right whitespace-nowrap max-sm:py-0.5 max-sm:text-left',
                  rowLink ? 'max-sm:px-0' : 'max-sm:absolute max-sm:top-1 max-sm:right-0',
                )}
              >
                {actions(row)}
              </TableCell>
            ) : null}
            {rowLink ? (
              <TableCell className="w-0 max-sm:absolute max-sm:top-2 max-sm:right-0 max-sm:w-auto">
                <ChevronRight className="size-4 text-muted-foreground" />
              </TableCell>
            ) : null}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
