import type { ReactNode } from 'react'
import { Plus } from 'lucide-react'
import { IconButton } from '@/components/icon-button'
import { Page } from '@/components/page'
import { SaveControls } from '@/components/save-controls'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { SettingsState } from '@/hooks/use-settings'

export type Column<T> = { key: string; title: string; render: (row: T) => ReactNode; className?: string }

type TableProps<T> = {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  actions?: (row: T) => ReactNode
  onRowClick?: (row: T) => void
}

export function DataTable<T>({ columns, rows, rowKey, actions, onRowClick }: TableProps<T>) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((c) => (
            <TableHead key={c.key} className={c.className}>
              {c.title}
            </TableHead>
          ))}
          {actions ? <TableHead className="w-0" /> : null}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow
            key={rowKey(row)}
            className={onRowClick ? 'cursor-pointer' : undefined}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
          >
            {columns.map((c) => (
              <TableCell key={c.key} className={c.className}>
                {c.render(row)}
              </TableCell>
            ))}
            {actions ? (
              <TableCell className="text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                {actions(row)}
              </TableCell>
            ) : null}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

type PageProps<T> = TableProps<T> & {
  title: string
  page: SettingsState
  onAdd?: () => void
  addLabel?: string
  children?: ReactNode
}

/** Template: list page. Rows with icon actions, one Add, one Save. */
export function ListPage<T>({ title, page, onAdd, addLabel = 'Add', children, ...table }: PageProps<T>) {
  return (
    <Page
      title={title}
      actions={
        <>
          {onAdd ? (
            <IconButton label={addLabel} variant="outline" size="icon" onClick={onAdd}>
              <Plus />
            </IconButton>
          ) : null}
          <SaveControls dirty={page.dirty} saving={page.saving} onSave={page.save} onReset={page.reset} />
        </>
      }
    >
      <DataTable {...table} />
      {children}
    </Page>
  )
}
