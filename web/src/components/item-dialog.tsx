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
  schema?: JsonSchema
  value?: unknown
  onChange?: (next: unknown) => void
  onSubmit: () => void
  canSubmit?: boolean
  children?: ReactNode
}

/** Add or edit one entry of a keyed map (entity, sensor, engine): the key, then its fields. */
export function ItemDialog(props: Props) {
  const { open, onOpenChange, title, keyLabel, keyValue, onKeyChange, schema, value, onChange, onSubmit } = props
  const canSubmit = props.canSubmit ?? keyValue.trim().length > 0
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="item-key">{keyLabel}</Label>
            <Input
              id="item-key"
              value={keyValue}
              disabled={!onKeyChange}
              onChange={(e) => onKeyChange?.(e.target.value)}
              autoFocus={Boolean(onKeyChange)}
            />
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
