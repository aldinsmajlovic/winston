import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { $api } from '@/api/client'
import { useOptions } from '@/hooks/use-options'

const KEY = 'winston.room'

function read(): string {
  try {
    return localStorage.getItem(KEY) ?? ''
  } catch {
    return ''
  }
}

function write(room: string) {
  try {
    localStorage.setItem(KEY, room)
  } catch {
    // private window or blocked storage: the room lasts this visit only
  }
}

/** The room this browser is in, shared with that room's box: `?room=` in the address (a wall panel's
 * bookmark) wins and is remembered, else the Talk page's last choice. Empty means no room. */
export function useRoom(): [string, (room: string) => void] {
  const [params, setParams] = useSearchParams()
  const fromUrl = params.get('room')?.trim()
  const [stored, setStored] = useState(read)

  useEffect(() => {
    if (fromUrl !== undefined) write(fromUrl)
  }, [fromUrl])

  const choose = useCallback(
    (room: string) => {
      write(room)
      setStored(room)
      if (params.has('room')) {
        const next = new URLSearchParams(params)
        next.delete('room')
        setParams(next, { replace: true })
      }
    },
    [params, setParams],
  )

  return [fromUrl ?? stored.trim(), choose]
}

/** A room name folded the way winstond matches rooms: case and spacing do not count. */
export const fold = (room: string) => room.trim().replace(/\s+/g, ' ').toLowerCase()

/** exposed is the area of that name's count ("5 exposed"), when Home Assistant has one. */
export type RoomChoice = { name: string; box: boolean; exposed?: string }

/** The rooms a screen can be in: the boxes' rooms and any a screen is in now, then Home Assistant's areas,
 * so a screen in a room without a box (a wall tablet) can be put there. A room without a box means the
 * area of its own name. */
export function useRoomChoices(): RoomChoice[] {
  const rooms = $api.useQuery('get', '/api/rooms').data ?? []
  const { choices } = useOptions('ha_areas')
  const exposed = new Map(choices.map((c) => [fold(c.value), c.hint ?? undefined]))
  const known = new Set(rooms.map((r) => fold(r.name)))
  return [
    ...rooms.map((r) => ({ ...r, exposed: exposed.get(fold(r.name)) })),
    ...choices.filter((c) => !known.has(fold(c.value))).map((c) => ({ name: c.value, box: false, exposed: c.hint ?? undefined })),
  ]
}
