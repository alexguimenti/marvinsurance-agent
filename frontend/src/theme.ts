export type ThemeChoice = 'light' | 'dark' | 'system'

const KEY = 'marvin-theme'

/** What this browser chose: light, dark or the same as the computer (the default). */
export function getThemeChoice(): ThemeChoice {
  const saved = localStorage.getItem(KEY)
  return saved === 'light' || saved === 'dark' ? saved : 'system'
}

function prefersDark(): boolean {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

/** Put the theme on the page, where the tokens read it from `data-theme`. */
export function applyTheme(choice: ThemeChoice = getThemeChoice()) {
  document.documentElement.dataset.theme = choice === 'system' ? (prefersDark() ? 'dark' : 'light') : choice
}

export function setThemeChoice(choice: ThemeChoice) {
  if (choice === 'system') localStorage.removeItem(KEY)
  else localStorage.setItem(KEY, choice)
  applyTheme(choice)
}

/** Follow the computer's own switch between light and dark while the choice is "the same as the computer". */
export function followComputerTheme(): () => void {
  const query = window.matchMedia?.('(prefers-color-scheme: dark)')
  if (!query) return () => {}
  const onChange = () => {
    if (getThemeChoice() === 'system') applyTheme('system')
  }
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}
