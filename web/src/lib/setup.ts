/** The setup wizard's steps before Done, by the id winstond keeps them under (api/setup_link.py): the name
 * the step list and Set up next give each, and the sentence the step opens with. */
export const SETUP_STEPS: Record<string, { title: string; intro: string }> = {
  brain: {
    title: 'Brain and voice',
    intro: 'What he thinks, speaks and hears with, picked for this computer. Change any of them here, or later in Settings.',
  },
  home_assistant: {
    title: 'Home Assistant',
    intro: 'Sign in to Home Assistant, then choose the devices he can see and control there.',
  },
}
