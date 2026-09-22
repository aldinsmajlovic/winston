/** "12 s", "3 min", "2 h": for how long something has run or will take. */
export function duration(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${Math.round(seconds / 3600)} h`
}

export function since(epochSeconds: number): string {
  return duration(Date.now() / 1000 - epochSeconds)
}
