// Motion that says something (docs/ui-guide.md#motion): where a link landed, what a save changed.

/** Smooth, unless the system asks to reduce motion. */
export const scrollBehavior = (): ScrollBehavior => (matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth')

/** Rings each element's card for a moment (`animate-flash` in index.css), again if it is still ringing. */
export function ring(elements: (Element | null)[]) {
  for (const card of new Set(elements.flatMap((el) => (el ? [el.closest('[data-slot="card"]') ?? el] : [])))) {
    card.classList.remove('animate-flash')
    void (card as HTMLElement).offsetWidth // a reflow, so adding it back restarts it
    card.classList.add('outline-2', 'outline-offset-4', 'outline-transparent', 'animate-flash')
  }
}
