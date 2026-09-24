import type { ReactNode } from 'react'
import { SchemaForm } from '@/components/schema-form'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { JsonSchema } from '@/lib/schema'

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  keyLabel: string
  keyValue: string
  onKeyChange?: (key: string) => void
  /** Replaces the key's text field, e.g. with a picker. */
  keyInput?: ReactNode
  schema?: JsonSchema
  value?: unknown
  onChange?: (next: unknown) => void
  onSubmit: () => void
  canSubmit?: boolean
  /** Keys already in the map; Done stays off while the key matches one other than `original`. */
  existing?: Iterable<string>
  /** The key being edited, which may keep its own name. */
  original?: string
  children?: ReactNode
}

/** Add or edit one entry of a keyed map (entity, sensor, engine): the key, then its fields. */
export function ItemDialog(props: Props) {
  const { open, onOpenChange, title, keyLabel, keyValue, onKeyChange, schema, value, onChange, onSubmit } = props
  const key = keyValue.trim()
  const duplicate = key !== '' && key !== props.original && new Set(props.existing).has(key)
  const canSubmit = (props.canSubmit ?? key.length > 0) && !duplicate
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="item-key">{keyLabel}</Label>
            {props.keyInput ?? (
              <Input
                id="item-key"
                value={keyValue}
                disabled={!onKeyChange}
                aria-invalid={duplicate}
                onChange={(e) => onKeyChange?.(e.target.value)}
                autoFocus={Boolean(onKeyChange)}
              />
            )}
            {duplicate ? <span className="text-sm text-destructive">Already in the list</span> : null}
          </div>
          {props.children}
          {schema && onChange ? <SchemaForm schema={schema} value={value} onChange={onChange} /> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canSubmit} onClick={onSubmit}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
