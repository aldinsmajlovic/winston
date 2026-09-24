import { useSchema } from '@/api/config'
import { section } from '@/lib/schema'

export type DriverChoice = { value: string; label: string; icon?: string }

/** Engine drivers with the labels and icons the config schema gives them, one source for every page. */
export function useDrivers(): DriverChoice[] {
  const schema = useSchema()
  if (!schema.data) return []
  const field = section(schema.data, ['engines', 'item']).properties?.driver as
    | { enum?: string[]; labels?: Record<string, string>; icons?: Record<string, string> }
    | undefined
  return (field?.enum ?? []).map((value) => ({ value, label: field?.labels?.[value] ?? value, icon: field?.icons?.[value] }))
}
