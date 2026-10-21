import {
  AudioLines,
  Bell,
  AudioWaveform,
  Blinds,
  Calendar,
  Camera,
  Circle,
  CircleAlert,
  CircleCheck,
  CircleDot,
  CirclePower,
  Cloud,
  CloudSun,
  Cpu,
  Download,
  Fan,
  Gauge,
  Globe,
  Hash,
  Layers,
  Lightbulb,
  List,
  ListTodo,
  Lock,
  MapPin,
  MapPinned,
  MessageSquare,
  Mic,
  Palette,
  Puzzle,
  RadioTower,
  RobotVacuum,
  ScrollText,
  Smartphone,
  Speaker,
  Sun,
  Terminal,
  Thermometer,
  Timer,
  ToggleLeft,
  ToggleRight,
  Tv,
  User,
  Volume2,
  Workflow,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import {
  siAnthropic,
  siClaude,
  siDiscord,
  siDocker,
  siGooglegemini,
  siHomeassistant,
  siHuggingface,
  siNvidia,
  siOllama,
  siVllm,
  type SimpleIcon,
} from 'simple-icons'
import { cn } from '@/lib/utils'

// Logos come from Simple Icons (CC0), drawn in the text colour like every other icon, so the one
// accent rule holds. Anything without a logo gets a lucide icon for its kind.
const brands: Record<string, SimpleIcon> = {
  anthropic: siAnthropic,
  claude: siClaude,
  discord: siDiscord,
  docker: siDocker,
  gemini: siGooglegemini,
  homeassistant: siHomeassistant,
  huggingface: siHuggingface,
  nvidia: siNvidia,
  parakeet: siNvidia,
  ollama: siOllama,
  vllm: siVllm,
}

const kinds: Record<string, LucideIcon> = {
  // engines and drivers
  cloud: Cloud,
  chatterbox: AudioWaveform,
  llamacpp: Layers,
  pocket: Cpu,
  process: Terminal,
  external: Globe,
  // channels
  satellite: RadioTower,
  // where a hint word comes from (stt.hint_words)
  assistant: User,
  voice: Mic,
  extension: Puzzle,
  // where a nudge can go (speak_up.phone)
  phone: Smartphone,
  notify: Bell,
  // Home Assistant domains
  light: Lightbulb,
  switch: ToggleRight,
  input_boolean: ToggleLeft,
  sensor: Gauge,
  binary_sensor: CircleDot,
  button: CirclePower,
  input_button: CirclePower,
  person: User,
  device_tracker: MapPin,
  zone: MapPinned,
  weather: CloudSun,
  sun: Sun,
  todo: ListTodo,
  calendar: Calendar,
  media_player: Tv,
  camera: Camera,
  climate: Thermometer,
  lock: Lock,
  cover: Blinds,
  fan: Fan,
  automation: Workflow,
  script: ScrollText,
  scene: Palette,
  conversation: MessageSquare,
  update: Download,
  number: Hash,
  input_number: Hash,
  select: List,
  input_select: List,
  tts: Volume2,
  stt: Mic,
  wake_word: AudioLines,
  assist_satellite: Speaker,
  event: Zap,
  timer: Timer,
  vacuum: RobotVacuum,
}

// Env variables: a status is an icon plus a colour.
const states: Record<string, { Icon: LucideIcon; className: string }> = {
  set: { Icon: CircleCheck, className: 'text-success' },
  missing: { Icon: CircleAlert, className: 'text-warning' },
}

/** A maker's logo from its Simple Icons path, in the text colour. Hidden from screen readers, since
 * every caller prints the name beside it. */
export function Logo({ path, className }: { path: string; className?: string }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className={cn('size-4 shrink-0 fill-current', className)}>
      <path d={path} />
    </svg>
  )
}

/** Whether a key has a kind icon of its own: an engine kind, a channel or an HA domain. */
// eslint-disable-next-line react-refresh/only-export-components -- the tool line asks it before drawing one
export const isKind = (name: string) => Object.hasOwn(kinds, name)

/** The logo or kind icon for a key: a brand, an engine kind, an HA domain or an env state. */
export function KindIcon({ name, className }: { name?: string | null; className?: string }) {
  if (!name) return null
  const brand = brands[name]
  if (brand) return <Logo path={brand.path} className={className} />
  const state = states[name]
  if (state) return <state.Icon aria-label={name} className={cn('size-4 shrink-0', state.className, className)} />
  const Icon = kinds[name] ?? Circle
  return <Icon aria-hidden className={cn('size-4 shrink-0', className)} />
}
