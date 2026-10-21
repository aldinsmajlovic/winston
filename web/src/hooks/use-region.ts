import { useConfig } from '@/api/config'
import { formatDate, formatDay, formatTime } from '@/lib/format'

/** Times and dates as the Region settings say (clock, day or month first), for every page. */
export function useRegion() {
  const region = useConfig().data?.region
  const clock = region?.time_format ?? 'auto'
  const order = region?.date_format ?? 'auto'
  return {
    time: (t: number | Date) => formatTime(t, clock),
    day: (t: number | Date) => formatDay(t, order),
    date: (t: number | Date) => formatDate(t, order),
  }
}
