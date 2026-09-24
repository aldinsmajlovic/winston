import { Search, X } from 'lucide-react'
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '@/components/ui/input-group'

type Props = { value: string; onChange: (value: string) => void; placeholder: string; label?: string; autoFocus?: boolean }

/** A search box for a settings list: the icon, the text and a clear button once something is typed. */
export function SearchField({ value, onChange, placeholder, label, autoFocus }: Props) {
  return (
    <InputGroup>
      <InputGroupAddon>
        <Search />
      </InputGroupAddon>
      <InputGroupInput
        role="searchbox"
        aria-label={label ?? placeholder}
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
      />
      {value ? (
        <InputGroupAddon align="inline-end">
          <InputGroupButton size="icon-xs" aria-label="Clear search" onClick={() => onChange('')}>
            <X />
          </InputGroupButton>
        </InputGroupAddon>
      ) : null}
    </InputGroup>
  )
}
