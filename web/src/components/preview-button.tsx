import { Play, Square } from 'lucide-react'
import { IconButton } from '@/components/icon-button'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

type Props = {
  /** The action's name: the tooltip of the icon button, the text of the text button. */
  label: string
  /** A text button ("Play a sample") instead of an icon with the label in its tooltip. */
  text?: boolean
  playing: boolean
  /** Why it cannot play now; the button is disabled with this as its tooltip. */
  blocked?: string
  onPlay: () => void
  onStop: () => void
}

/** Play, or Stop while playing: a preview of unsaved settings (usePreview). */
export function PreviewButton({ label, text, playing, blocked, onPlay, onStop }: Props) {
  const icon = playing ? <Square /> : <Play />
  const onClick = playing ? onStop : onPlay
  if (!blocked && !text) {
    return (
      <IconButton label={label} type="button" variant="outline" size="icon" onClick={onClick}>
        {icon}
      </IconButton>
    )
  }
  const button = text ? (
    <Button type="button" variant="outline" size="sm" disabled={!!blocked} onClick={onClick}>
      {icon}
      {playing ? 'Stop' : label}
    </Button>
  ) : (
    <Button type="button" variant="outline" size="icon" aria-label={label} disabled onClick={onClick}>
      {icon}
    </Button>
  )
  if (!blocked) return button
  // A disabled button gets no pointer events, so the tooltip hangs on a focusable wrapper.
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className="inline-flex">
          {button}
        </span>
      </TooltipTrigger>
      <TooltipContent>{blocked}</TooltipContent>
    </Tooltip>
  )
}
