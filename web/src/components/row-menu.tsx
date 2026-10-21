import { EllipsisVertical } from 'lucide-react'
import { IconButton } from '@/components/icon-button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'

type Item = { label: string; onSelect: () => void; destructive?: boolean; disabled?: boolean }

/** A row's actions as worded menu items behind a quiet button that is always there, so they reach
 * by tap and keyboard alike (never on hover only). */
export function RowMenu({ name, items }: { name: string; items: Item[] }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <IconButton label={`Actions for ${name}`} className="text-muted-foreground">
          <EllipsisVertical />
        </IconButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {items.map((item) => (
          <DropdownMenuItem
            key={item.label}
            variant={item.destructive ? 'destructive' : 'default'}
            disabled={item.disabled}
            onSelect={item.onSelect}
          >
            {item.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
