import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useConfirm } from '@/hooks/use-confirm'
import { useSecretStatus, useSecretsDraft } from '@/hooks/use-secrets'

type Props = {
  id?: string
  /** The variable(s) this field stores. With several, `resolve` picks one from the pasted value and
   * the field counts as set when any of them is. */
  names: string[]
  /** Which of `names` a pasted value belongs to (e.g. by its prefix); the first name otherwise. */
  resolve?: (value: string) => string | undefined
  /** What the key is ("Bot token"), for the placeholder and the Remove confirmation. */
  label: string
  /** What the empty field says while no key is saved, in place of "Paste the bot token". */
  placeholder?: string
  disabled?: boolean
  /** Called with the name a value was pasted under, e.g. to write that name into the config. */
  onPaste?: (name: string) => void
  /** Not set is fine (another sign-in works), so it never says Not set. */
  optional?: boolean
  /** Its section is switched off, so Not set is no warning yet. */
  quiet?: boolean
}

/** "Bot token" to "bot token" for use mid-sentence, leaving names and acronyms ("API key",
 * "ListenBrainz token") as written. */
function inSentence(label: string): string {
  const rest = label.split(' ')[0].slice(1)
  return rest === rest.toLowerCase() ? label.charAt(0).toLowerCase() + label.slice(1) : label
}

/** A key pasted into the settings: kept in the secrets draft until Save writes it to data/.env, then
 * never shown again. Beside it a word says whether it is saved; Remove deletes it at once. */
export function SecretField({ id, names, resolve, label, placeholder, disabled, onPaste, optional, quiet }: Props) {
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
      title: `Remove the saved ${inSentence(label)}?`,
      lines: [
        'It is deleted from this computer now, without waiting for Save.',
        ...(restarts.length ? ['What is running keeps using it until it restarts.'] : []),
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
    toast.success(`Removed the ${inSentence(label)}`)
  }

  const empty = isSet ? 'Paste to replace it' : (placeholder ?? (optional ? 'Optional' : `Paste the ${inSentence(label)}`))
  return (
    <div className="flex items-center gap-2">
      <Input
        id={id}
        type="password"
        autoComplete="new-password"
        spellCheck={false}
        placeholder={empty}
        value={typed}
        disabled={disabled}
        onChange={(e) => change(e.target.value.trim())}
      />
      {status.isLoading ? null : isSet ? (
        <span className="text-sm whitespace-nowrap text-success">Saved</span>
      ) : optional || quiet || typed ? null : (
        <span className="text-sm whitespace-nowrap text-warning">Not set</span>
      )}
      {isSet ? (
        <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={() => void remove()}>
          Remove
        </Button>
      ) : null}
    </div>
  )
}
