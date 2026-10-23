// Typed client for winstond, generated from its OpenAPI description (pnpm api).
import createFetchClient from 'openapi-fetch'
import createClient from 'openapi-react-query'
import type { components, paths } from './schema'

export const fetchClient = createFetchClient<paths>({ baseUrl: '/' })
export const $api = createClient(fetchClient)

export type Status = components['schemas']['Status']
export type EngineStatus = components['schemas']['EngineStatus']
export type EngineState = EngineStatus['state']
export type Config = components['schemas']['WinstonConfig']
export type EngineConfig = components['schemas']['EngineConfig']
export type ExposedEntity = components['schemas']['ExposedEntity']
export type HomeAssistantConfig = components['schemas']['HomeAssistantConfig']
export type ValidationError = components['schemas']['HTTPValidationError']
export type Preset = components['schemas']['Preset']
export type Presets = components['schemas']['Presets']
export type Options = components['schemas']['Options']
export type LiveEvent = components['schemas']['LiveEvent']
export type MemoryFact = components['schemas']['MemoryFact']
export type MemoryTopic = components['schemas']['MemoryTopic']
export type ConfigResult = components['schemas']['ConfigResult']
export type PersonaRename = components['schemas']['PersonaRename']
export type ConfigConflict = components['schemas']['ConfigConflict']
export type SecretStatus = components['schemas']['SecretStatus']
export type SecretResult = components['schemas']['SecretResult']
export type ExtensionView = components['schemas']['ExtensionView']
export type TurnRecord = components['schemas']['TurnRecord']
export type TurnMark = components['schemas']['TurnMark']
export type TurnDay = components['schemas']['TurnDay']
export type InstallPlan = components['schemas']['InstallPlan']

/** A 422 `loc` without FastAPI's leading 'body', 'query' or 'path'; the config routes already start
 * at the config root. */
export function locPath(loc: (string | number)[]): (string | number)[] {
  return typeof loc[0] === 'string' && ['body', 'query', 'path'].includes(loc[0]) ? loc.slice(1) : loc
}

export function errorText(error: unknown): string {
  const detail = (error as ValidationError | undefined)?.detail
  if (Array.isArray(detail)) {
    // Only the reasons: a refused config save already shows each beside its field.
    return detail.map((d) => d.msg).join('\n')
  }
  if (typeof detail === 'string') return detail
  // fetch's own failure (Chrome, Firefox, Safari wording): the server did not answer at all.
  if (error instanceof TypeError && /Failed to fetch|NetworkError|Load failed/.test(error.message)) {
    return "Can't reach Winston. Check that it is running, then try again."
  }
  if (error instanceof Error) return error.message
  return 'Request failed'
}
