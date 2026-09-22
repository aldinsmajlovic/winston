import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tailwind from 'eslint-plugin-better-tailwindcss'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// docs/ui-guide.md, enforced: no inline styles, no custom CSS classes, no em dashes.
// The regex source is an escape so this file holds no em dash itself.
const noEmDash = 'No em dashes (docs/ui-guide.md)'
const noStyle = [{ propName: 'style', message: 'No inline styles (docs/ui-guide.md)' }]

export default defineConfig([
  globalIgnores(['dist', 'src/api/schema.d.ts']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    plugins: { react, 'better-tailwindcss': tailwind },
    languageOptions: {
      globals: globals.browser,
    },
    settings: {
      react: { version: 'detect' },
      'better-tailwindcss': { entryPoint: 'src/index.css' },
    },
    rules: {
      'react/forbid-dom-props': ['error', { forbid: noStyle }],
      'react/forbid-component-props': ['error', { forbid: noStyle }],
      'better-tailwindcss/no-unknown-classes': 'error',
      'no-restricted-syntax': [
        'error',
        { selector: 'Literal[value=/\\u2014/]', message: noEmDash },
        { selector: 'TemplateElement[value.raw=/\\u2014/]', message: noEmDash },
        { selector: 'JSXText[value=/\\u2014/]', message: noEmDash },
      ],
    },
  },
  {
    // The component library itself (shadcn output, kept as generated): it sets CSS variables
    // through style props and exports variants next to components.
    files: ['src/components/ui/**', 'src/hooks/use-mobile.ts'],
    rules: {
      'react/forbid-dom-props': 'off',
      'react/forbid-component-props': 'off',
      'better-tailwindcss/no-unknown-classes': 'off',
      'react-refresh/only-export-components': 'off',
      'react-hooks/set-state-in-effect': 'off',
    },
  },
])
