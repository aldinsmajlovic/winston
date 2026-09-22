// Build guard for docs/ui-guide.md: no em dashes anywhere, no CSS beyond the theme tokens in
// src/index.css, no inline styles in HTML. ESLint covers inline styles and custom classes in TSX.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, relative } from 'node:path'

const root = fileURLToPath(new URL('..', import.meta.url))
const skip = new Set(['node_modules', 'dist', '.git'])
const emDash = new RegExp(String.fromCodePoint(0x2014))
const problems = []

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    if (skip.has(name)) continue
    const path = join(dir, name)
    if (statSync(path).isDirectory()) yield* walk(path)
    else yield path
  }
}

for (const file of walk(root)) {
  const rel = relative(root, file)
  if (!/\.(ts|tsx|css|html|json|md|mjs|js|yaml)$/.test(rel)) continue
  const text = readFileSync(file, 'utf8')
  text.split('\n').forEach((line, i) => {
    if (emDash.test(line)) problems.push(`${rel}:${i + 1}: em dash`)
  })
  if (rel.endsWith('.css') && rel !== 'src/index.css') {
    problems.push(`${rel}: only src/index.css may contain CSS (theme tokens)`)
  }
  if (rel === 'src/index.css') {
    text.split('\n').forEach((line, i) => {
      if (/^\s*\.(?!dark\b)[A-Za-z_-]/.test(line)) problems.push(`${rel}:${i + 1}: custom class selector`)
    })
  }
  if (rel.endsWith('.html') && /\sstyle=/.test(text)) problems.push(`${rel}: inline style`)
}

if (problems.length) {
  console.error(problems.join('\n'))
  process.exit(1)
}
