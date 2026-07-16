import { useEffect, useRef } from "react";

/** A stable debounced wrapper: each call resets a timer; the latest call wins after `delayMs` of quiet. The
 *  returned function has a STABLE identity (safe as an effect dependency), and always invokes the LATEST `fn`
 *  closure (no stale captures). The pending timer is cleared on unmount — so a `key`-remount (e.g. a thesis
 *  switch) can never fire a late call carrying the previous mount's state. Mirrors the repo's `useRef`-held-
 *  timer + explicit-clear discipline (ChainEditor's poll timeout). No debounce dependency exists in the tree. */
export function useDebouncedCallback<A extends unknown[]>(
  fn: (...args: A) => void,
  delayMs: number,
): (...args: A) => void {
  const timer = useRef<number | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn; // always call the freshest closure

  const debounced = useRef((...args: A) => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      fnRef.current(...args);
    }, delayMs);
  });

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  return debounced.current;
}
