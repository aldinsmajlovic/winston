// Writes the mark at rest to public/favicon.svg and docs/images/logo.svg from src/lib/mark.ts.
// Run after changing the geometry: node scripts/mark-svg.ts
import { writeFileSync } from 'node:fs'
import { MARK, REST, paths } from '../src/lib/mark.ts'

const d = paths(REST)
// The page's text colour: near-black, near-white on a dark ground.
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<style>svg{color:#191816}@media (prefers-color-scheme:dark){svg{color:#EFEEEB}}</style>
<defs><mask id="open"><rect width="100" height="100" fill="#fff"/><path d="${d.upper}" fill="#000"/></mask></defs>
<circle cx="50" cy="50" r="${MARK.radius - 2 * MARK.ring}" fill="currentColor" mask="url(#open)"/>
<circle cx="50" cy="50" r="${MARK.radius - MARK.ring / 2}" fill="none" stroke="currentColor" stroke-width="${MARK.ring}"/>
</svg>
`
writeFileSync(new URL('../public/favicon.svg', import.meta.url), svg)
writeFileSync(new URL('../../docs/images/logo.svg', import.meta.url), svg)
