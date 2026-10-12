/** A message passed along in navigation state (e.g. "Night auditor was deleted."). */
export function noticeFrom(state: unknown): string | null {
  if (typeof state === 'object' && state !== null && 'notice' in state) {
    const { notice } = state
    if (typeof notice === 'string') return notice
  }
  return null
}
