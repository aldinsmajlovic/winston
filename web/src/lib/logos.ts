// Every Simple Icons logo, each loaded on first use. Its own module, loaded only when an extension
// names a logo: the map alone is 400 KB, and the logos 5 MB.
export const LOGOS = import.meta.glob<string>('/node_modules/simple-icons/icons/*.svg', { query: '?raw', import: 'default' })
