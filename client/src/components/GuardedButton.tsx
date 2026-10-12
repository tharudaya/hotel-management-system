import { type ComponentProps, useId } from 'react'

type GuardedButtonProps = Omit<ComponentProps<'button'>, 'type'> & {
  /** Why the action is not allowed; null/undefined when it is. */
  blockedReason?: string | null | undefined
}

/**
 * A button that, when not allowed, stays focusable (aria-disabled, not disabled) and
 * explains why in a tooltip shown on hover and keyboard focus, also exposed as its
 * accessible description. The element is the same either way, so switching between
 * allowed and blocked never loses keyboard focus.
 */
export function GuardedButton({
  blockedReason,
  onClick,
  className = '',
  children,
  ...rest
}: GuardedButtonProps) {
  const tooltipId = useId()
  const isBlocked = Boolean(blockedReason)
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        {...rest}
        aria-disabled={isBlocked ? true : undefined}
        aria-describedby={isBlocked ? tooltipId : undefined}
        onClick={(e) => {
          if (isBlocked) e.preventDefault()
          else onClick?.(e)
        }}
        className={isBlocked ? `${className} cursor-not-allowed opacity-60` : className}
      >
        {children}
      </button>
      {isBlocked && (
        <span
          id={tooltipId}
          role="tooltip"
          className="pointer-events-none invisible absolute bottom-full left-1/2 z-20 mb-2 w-60 -translate-x-1/2 rounded-md bg-slate-900 px-3 py-2 text-xs text-white shadow-lg group-focus-within:visible group-hover:visible"
        >
          {blockedReason}
        </span>
      )}
    </span>
  )
}
