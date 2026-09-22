import { Save, Undo2 } from 'lucide-react'
import { IconButton } from '@/components/icon-button'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

type SaveProps = { dirty: boolean; saving: boolean; onSave: () => void; onReset: () => void }

export function SaveControls({ dirty, saving, onSave, onReset }: SaveProps) {
  return (
    <>
      <IconButton label="Discard changes" disabled={!dirty || saving} onClick={onReset}>
        <Undo2 />
      </IconButton>
      <Button size="sm" disabled={!dirty || saving} onClick={onSave}>
        <Save data-icon="inline-start" />
        Save
      </Button>
    </>
  )
}

type AdvancedProps = { checked: boolean; onChange: (checked: boolean) => void }

export function AdvancedToggle({ checked, onChange }: AdvancedProps) {
  return (
    <div className="flex items-center gap-2">
      <Switch id="advanced" checked={checked} onCheckedChange={onChange} />
      <Label htmlFor="advanced">Advanced</Label>
    </div>
  )
}
