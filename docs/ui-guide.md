# UI guide

The web UI is a settings app, not a product page. It is built with one component library and no
hand-written CSS beyond theme tokens, so pages look the same by construction.

## Stack

React, Vite, TypeScript. shadcn/ui components on Tailwind. lucide icons. TanStack Query for data.
An OpenAPI-generated client for `winstond`. JSON-Schema forms with a shadcn theme for settings.

## Rules

- Only shadcn components and the three page templates: settings page, status page, list page.
- Labels, values and controls. No welcome text, no explanations, no marketing. A hint is one line and
  appears only under Advanced.
- Icons for actions and state. A status is an icon plus a colour, never a sentence.
- One accent colour. Semantic colours (ok, warning, error) are not the accent.
- No inline styles, no custom CSS classes, no em dashes. The build fails on all three.
- Basic first, Advanced behind a toggle on every page. The schema decides which is which.
- Destructive or slow actions confirm once, say what they cost ("restarts the brain, about 3 minutes").
- Light and dark from the library's theme; nothing hand-tuned per theme.

## Build

`pnpm build` runs the guardrails (`scripts/check.mjs`: em dashes in any file, CSS outside
`src/index.css`, inline styles in HTML), ESLint (`style` prop, classes Tailwind does not know, em
dashes in code), the type check and Vite. shadcn's generated `src/components/ui` is the library and
is exempt. `pnpm api` regenerates the client from `winston api schema`.

## Templates

- Settings page: `SettingsPage` with one `SectionForm` per config section, a form from the pruned
  schema. `useSettings` holds the draft shared across pages and the save flow: dry run, confirm the
  restarts with their cost, save, restart.
- Status page: `pages/status.tsx`. `StateIcon` for state, icon actions, one gauge.
- List page: `ListPage` or `DataTable` plus `ItemDialog` for keyed maps (entities, sensors, engines).
