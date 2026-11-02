import type { FrozenResponse } from "./contract";

const canonical = (query: URLSearchParams) => [...query.entries()]
  .sort(([ak, av], [bk, bv]) => ak.localeCompare(bk) || av.localeCompare(bv));
const queryKey = (query: URLSearchParams) => JSON.stringify(canonical(query));

/** This fetch has no network fallback: a new query can only read a recorded response. */
export function frozenFetch(responses: FrozenResponse[], onError: (message: string) => void): typeof fetch {
  return async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), location.origin), init);
    const method = (init?.method ?? request.method).toUpperCase();
    const url = new URL(request.url, location.origin);
    const path = url.pathname.replace(/^\/api(?=\/)/, "");
    const match = method === "GET" && url.origin === location.origin && responses.find((r) =>
      r.method === "GET" && r.path === path && queryKey(new URLSearchParams(r.query)) === queryKey(url.searchParams));
    if (!match) {
      const message = `Frozen snapshot refused ${method} ${path}${url.search}`;
      onError(message);
      throw new Error(message);
    }
    return new Response(JSON.stringify(match.body), {
      status: match.status, headers: { "content-type": "application/json" },
    });
  };
}
