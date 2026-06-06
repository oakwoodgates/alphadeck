import { useTheses } from "./api/hooks";

// M3b PR-1 is the scaffold: this view only proves the wiring — generated types -> typed client ->
// TanStack Query -> render, against the live API. The Cockpit (PR-2) and Board (PR-3) replace it.
export function App() {
  const { data, isLoading, error } = useTheses();

  return (
    <main className="min-h-screen p-8">
      <h1 className="text-xl font-semibold text-neutral-100">Alpha Deck</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Frontend scaffold — wiring check. Cockpit and Board land next.
      </p>

      <section className="mt-6">
        <h2 className="text-xs font-medium uppercase tracking-wide text-neutral-500">Theses</h2>
        {isLoading && <p className="mt-2 text-neutral-400">Loading…</p>}
        {error && (
          <p className="mt-2 text-red-400">
            API not reachable — is the backend running on :8000 (and seeded)?
          </p>
        )}
        <ul className="mt-2 space-y-1">
          {data?.map((thesis) => (
            <li key={thesis.id} className="text-neutral-200">
              <span className="font-mono text-neutral-400">{thesis.ticker ?? "—"}</span> {thesis.name}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
