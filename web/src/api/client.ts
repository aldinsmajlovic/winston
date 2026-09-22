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
export type SensorPair = components['schemas']['SensorPair']
export type HomeAssistantConfig = components['schemas']['HomeAssistantConfig']
export type ValidationError = components['schemas']['HTTPValidationError']

export function errorText(error: unknown): string {
  const detail = (error as ValidationError | undefined)?.detail
  if (Array.isArray(detail)) {
    return detail.map((d) => `${d.loc.slice(1).join('.')}: ${d.msg}`).join('\n')
  }
  if (typeof detail === 'string') return detail
  if (error instanceof Error) return error.message
  return 'Request failed'
}
