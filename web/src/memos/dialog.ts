/** Keep keyboard focus inside the active modal without a global key listener. */
export function trapDialogTab(event: KeyboardEvent): void {
  if (event.key !== 'Tab') return
  const dialog = event.currentTarget as HTMLElement
  const stops = [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]')].filter(element => element.getClientRects().length)
  const first = stops[0], last = stops.at(-1)
  if (!first || !last) { event.preventDefault(); return }
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
