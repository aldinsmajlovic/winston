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
