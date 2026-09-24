import { toast } from 'sonner'
import { CircleAlert, CircleCheck, X } from 'lucide-react'
import { IconButton } from '@/components/icon-button'
import { Input } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useConfirm } from '@/hooks/use-confirm'
import { useSecretStatus, useSecretsDraft } from '@/hooks/use-secrets'

type Props = {
  id?: string
  /** The variable(s) this field stores. With several, `resolve` picks one from the pasted value and
   * the field counts as set when any of them is. */
  names: string[]
  /** Which of `names` a pasted value belongs to (e.g. by its prefix); the first name otherwise. */
  resolve?: (value: string) => string | undefined
  /** What the key is, for the Remove confirmation ("API key"). */
  label: string
  placeholder?: string
  disabled?: boolean
  /** Called with the name a value was pasted under, e.g. to write that name into the config. */
  onPaste?: (name: string) => void
  /** Not set is fine (another sign-in works), so only Set shows an icon. */
  optional?: boolean
}

/** A key pasted into the settings: kept in the secrets draft until Save writes it to data/.env, then
 * never shown again. Beside it an icon says whether the variable is set; Remove deletes it at once. */
export function SecretField({ id, names, resolve, label, placeholder = 'Paste a key', disabled, onPaste, optional }: Props) {
  const ask = useConfirm()
  const status = useSecretStatus()
  const draft = useSecretsDraft()
  const stored = names.filter((n) => status.byName.get(n)?.set)
  const isSet = stored.length > 0
  const typedName = names.find((n) => n in draft.pending)
  const typed = typedName ? draft.pending[typedName] : ''

  const change = (value: string) => {
    const name = (value && resolve?.(value)) || names[0]
    draft.discard(names.filter((n) => n !== name))
    draft.set(name, value)
    if (value) onPaste?.(name)
  }

  const remove = async () => {
    const restarts = [...new Set(stored.flatMap((n) => status.byName.get(n)?.restarts ?? []))].filter((r) => r !== 'control')
    const choice = await ask({
      title: `Remove the ${label.toLowerCase()}?`,
      lines: [
        `Deletes ${stored.join(' and ')} from data/.env.`,
        ...(restarts.length
          ? [`Running ${restarts.join(', ')} ${restarts.length === 1 ? 'keeps' : 'keep'} the old one until restarted.`]
          : []),
      ],
      choices: [{ label: 'Remove', value: 'yes', destructive: true }],
    })
    if (!choice) return
    for (const name of stored) {
      const why = await draft.remove(name)
      if (why) {
        toast.error(why)
        return
      }
    }
    toast.success(`Removed the ${label.toLowerCase()}`)
  }

  return (
    <div className="flex items-center gap-2">
      <Input
        id={id}
        type="password"
        autoComplete="new-password"
        spellCheck={false}
        placeholder={placeholder}
        value={typed}
        disabled={disabled}
        onChange={(e) => change(e.target.value.trim())}
      />
      {isSet || !optional ? <SecretState set={isSet} loading={status.isLoading} /> : null}
      {isSet ? (
        <IconButton type="button" label="Remove" disabled={disabled} onClick={() => void remove()}>
          <X />
        </IconButton>
      ) : null}
    </div>
  )
}

/** Set or not set: an icon plus a colour, the words in its tooltip. */
function SecretState({ set, loading }: { set: boolean; loading: boolean }) {
  if (loading) return null
  const Icon = set ? CircleCheck : CircleAlert
  const text = set ? 'Set' : 'Not set'
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">
          <Icon className={set ? 'size-4 text-success' : 'size-4 text-warning'} aria-label={text} />
        </span>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  )
}
