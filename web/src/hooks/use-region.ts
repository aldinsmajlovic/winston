import { useConfig } from '@/api/config'
import { formatDate, formatDay, formatTime } from '@/lib/format'

/** Times and dates as the Region settings say (clock, day or month first), for every page. */
export function useRegion() {
  const region = useConfig().data?.region
  const clock = region?.time_format ?? 'auto'
  const order = region?.date_format ?? 'auto'
  const time = (t: number | Date) => formatTime(t, clock)
  const day = (t: number | Date) => formatDay(t, order)
  return {
    time,
    day,
    date: (t: number | Date) => formatDate(t, order),
    /** "14:02" today, "Mon 19 Oct 08:00" on another day. */
    when: (t: number | Date) => {
      const d = typeof t === 'number' ? new Date(t * 1000) : t
      return d.toDateString() === new Date().toDateString() ? time(d) : `${day(d)} ${time(d)}`
    },
  }
}
