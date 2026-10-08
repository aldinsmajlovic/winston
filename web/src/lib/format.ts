/** "12 s", "3 min", "2 h": for how long something has run or will take. */
export function duration(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${Math.round(seconds / 3600)} h`
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
