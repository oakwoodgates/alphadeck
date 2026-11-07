// The login door's one signal (docs/AUTH.md): "the API just answered 401". The openapi-fetch middleware in
// api/client.ts emits it; the App listens, clears the query cache and goes to /login. A tiny in-memory emitter,
// no browser storage: the HttpOnly session cookie is the only client state.

type Listener = () => void;

const listeners = new Set<Listener>();

/** Subscribe to "the API answered 401"; returns the unsubscribe. */
export function onUnauthorized(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Tell every listener the API answered 401. */
export function emitUnauthorized(): void {
  for (const l of Array.from(listeners)) l();
}
