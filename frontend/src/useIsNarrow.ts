import { useEffect, useState } from 'react'

/** Phone-width breakpoint, matching the `@media (max-width: 720px)` block in index.css.
 *
 * Some layout choices can't be made in CSS — antd's Form `layout` and a Table's column set are
 * props, not styles — so the same threshold has to be readable from JS. Keep the two in step. */
export const NARROW_MAX_WIDTH = 720

export function useIsNarrow(maxWidth: number = NARROW_MAX_WIDTH): boolean {
  const [narrow, setNarrow] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(`(max-width: ${maxWidth}px)`).matches)

  useEffect(() => {
    const query = window.matchMedia(`(max-width: ${maxWidth}px)`)
    const update = () => setNarrow(query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [maxWidth])

  return narrow
}
