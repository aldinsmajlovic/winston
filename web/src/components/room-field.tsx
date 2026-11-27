import { Combobox, type Choice } from '@/components/combobox'
import { Input } from '@/components/ui/input'

type Props = { id: string; value: string; onChange: (room: string) => void; areas: Choice[] }

/** A room box's room: Home Assistant's areas to pick from, or a name typed; a plain text field without them. */
export function RoomField({ id, value, onChange, areas }: Props) {
  if (!areas.length) return <Input id={id} value={value} placeholder="living room" onChange={(e) => onChange(e.target.value)} />
  return <Combobox id={id} value={value} choices={areas} onChange={onChange} custom placeholder="Pick or type a room" />
}
