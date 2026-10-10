import type { ReactNode } from 'react'
import type { Preset } from '@/api/client'
import { InfoTip } from '@/components/info-tip'
import { KindIcon } from '@/components/kind-icon'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { SettingsState } from '@/hooks/use-settings'
import { applyPreset, CUSTOM, matchPreset, type Kind } from '@/lib/presets'

type Props = { page: SettingsState; kind: Kind; title: string; presets: Preset[]; children?: ReactNode }

/** One slot (brain, speaking, hearing): what runs it as a dropdown of presets, then the slot's own
 * controls. Picking a preset rewrites the engine block and the settings that go with it. */
export function SlotCard({ page, kind, title, presets, children }: Props) {
  const current = matchPreset(kind, page.value, presets)
  const preset = presets.find((p) => p.id === current)
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={`${kind}-engine`}>Engine</Label>
            <InfoTip text={preset?.hint ?? 'Your own engine block; edit it under Advanced.'} />
          </div>
          <Select
            value={current}
            onValueChange={(id) => {
              const p = presets.find((x) => x.id === id)
              if (p) page.update((c) => applyPreset(c, kind, p))
            }}
          >
            <SelectTrigger id={`${kind}-engine`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {presets.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  <KindIcon name={p.icon} />
                  {p.title}
                </SelectItem>
              ))}
              {/* Only to name a config no preset matches; nothing to pick (edit it under Advanced). */}
              {current === CUSTOM ? (
                <SelectItem value={CUSTOM} disabled>
                  Custom
                </SelectItem>
              ) : null}
            </SelectContent>
          </Select>
        </div>
        {children}
      </CardContent>
    </Card>
  )
}
