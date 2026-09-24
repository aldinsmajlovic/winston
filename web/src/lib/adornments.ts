import { createContext, type ReactNode } from 'react'

/** Controls a page puts beside a field or on a section heading inside a form (SectionForm
 * `adornments`), by the field's DOM id. A context rather than rjsf's formContext, so they re-render
 * with the page's state (a play button turning into stop). */
export const Adornments = createContext<Record<string, ReactNode>>({})
