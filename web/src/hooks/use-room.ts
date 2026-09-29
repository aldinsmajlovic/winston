import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'

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
