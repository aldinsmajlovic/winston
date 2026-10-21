/** "12 s", "3 min", "2 h": for how long something has run or will take. A no-break space keeps the
 * number with its unit when a narrow cell wraps. */
export function duration(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))}\u00a0s`
  if (seconds < 5400) return `${Math.round(seconds / 60)}\u00a0min`
  return `${Math.round(seconds / 3600)}\u00a0h`
}

export function since(epochSeconds: number): string {
  return duration(Date.now() / 1000 - epochSeconds)
}

/** "living room" -> "Living room". */
export function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** "reasoning_effort" -> "Reasoning effort", for a key the schema gives no title. */
export function humanize(key: string): string {
  return capitalize(key.replaceAll('_', ' '))
}

/** "Brain, Voice and Hearing". */
export function joinNames(names: string[]): string {
  return names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

export type Clock = 'auto' | '24h' | '12h'
export type DateOrder = 'auto' | 'day_month' | 'month_day'

/** Epoch seconds, as the API gives times, or a Date. */
const toDate = (t: number | Date) => (typeof t === 'number' ? new Date(t * 1000) : t)

const CLOCKS: Record<Clock, Intl.DateTimeFormatOptions> = {
  auto: { hour: 'numeric', minute: '2-digit' },
  '24h': { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' },
  '12h': { hour: 'numeric', minute: '2-digit', hourCycle: 'h12' },
}

// A set order reads as British or American English; auto is the browser's own language.
const dateLocale = (order: DateOrder) => (order === 'day_month' ? 'en-GB' : order === 'month_day' ? 'en-US' : undefined)

/** "14:25" or "2:25 PM", by the Region's clock; auto is the browser's. */
export function formatTime(t: number | Date, clock: Clock = 'auto'): string {
  return toDate(t).toLocaleTimeString([], CLOCKS[clock])
}

/** "Sat 17 Oct" or "Sat, Oct 17", by the Region's date order. */
export function formatDay(t: number | Date, order: DateOrder = 'auto'): string {
  return toDate(t).toLocaleDateString(dateLocale(order), { weekday: 'short', day: 'numeric', month: 'short' })
}

/** "23 Sep" or "Sep 23", by the Region's date order. */
export function formatDate(t: number | Date, order: DateOrder = 'auto'): string {
  return toDate(t).toLocaleDateString(dateLocale(order), { day: 'numeric', month: 'short' })
}
