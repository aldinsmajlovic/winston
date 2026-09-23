// Writes the mark at rest to public/favicon.svg and docs/images/logo.svg from src/lib/mark.ts.
// Run after changing the geometry: node scripts/mark-svg.ts
import { writeFileSync } from 'node:fs'
import { MARK, REST, paths } from '../src/lib/mark.ts'

const d = paths({ ...REST, level: 1 })
const rim = MARK.radius - 1.5 * MARK.ring
const svg = (dark: string) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<style>.ch{stop-color:#40454F}.cl{stop-color:#2A2E35}${dark}</style>
<defs>
<clipPath id="disc"><circle cx="50" cy="50" r="${MARK.radius}"/></clipPath>
<mask id="rim"><rect width="100" height="100" fill="#fff"/><circle cx="50" cy="50" r="${rim}" fill="none" stroke="#000" stroke-width="${MARK.ring}"/></mask>
<linearGradient id="sky" gradientUnits="userSpaceOnUse" x1="50" y1="4" x2="50" y2="96"><stop offset="0" stop-color="#97BAD8"/><stop offset="0.6" stop-color="#C9DDEE"/></linearGradient>
<linearGradient id="coal" gradientUnits="userSpaceOnUse" x1="50" y1="4" x2="50" y2="96"><stop offset="0.4" class="ch"/><stop offset="1" class="cl"/></linearGradient>
</defs>
<g mask="url(#rim)"><g clip-path="url(#disc)"><rect width="100" height="100" fill="url(#coal)"/><path d="${d.upper}" fill="url(#sky)"/></g></g>
</svg>
`
const onDark = '@media (prefers-color-scheme:dark){.ch{stop-color:#6C7380}.cl{stop-color:#59606C}}'
writeFileSync(new URL('../public/favicon.svg', import.meta.url), svg(onDark))
writeFileSync(new URL('../../docs/images/logo.svg', import.meta.url), svg(onDark))
