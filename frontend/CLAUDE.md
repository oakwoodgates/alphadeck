# Frontend rules

Rules for work under `frontend/`. The root `CLAUDE.md` safety rules and conventions apply here too.

## Interaction principles (WB#1 to WB#3)

Rules for the user-facing editor, separate from the invariants. Invariants protect trust: the system must never lie, drop or auto-decide, and breaking one corrupts a decision. These protect workflow: the editor must be reversible, honest and non-vanishing, and breaking one is friction. Code cites them as WB#1, WB#2 and WB#3.

**WB#1 Reversibility: every user action has a visible inverse.** Every state change the user makes in the Workbench has a visible way back. No action strands the user in a state they cannot leave without a re-roll, which costs API credits, or a full restart. The way back returns to the prior state and never destroys data: include and exclude, sign off and withdraw the sign-off, add (To-Review to Placed) and send back (Placed to To-Review). If a new action cannot be undone, it is not done.

**WB#2 Keep it visible: pruning hides, it never vanishes.** Excluding, skipping or de-selecting a name grays it and keeps it on screen, reversible in one click. It does not delete the row. The user is judging a universe and needs to see what they set aside. This is invariant 9 applied to the interface: invariant 9 keeps the system from dropping names, this keeps the interface from dropping them. A true hide-from-view is only ever an explicit, reversible filter, never the default and never a delete.

**WB#3 Honest loudness: loudness marks the exception, not the rule.** A badge or flag true of every row carries no information and is noise. Surface the rare keeper and collapse the common noise quietly (and the reverse, per bucket). A control that does not yet discriminate should not render. This is invariant 7 applied to the interface.

**The test for new editor work:** does the action have an inverse (WB#1), does it hide rather than destroy (WB#2), and does its loudness mark the exception rather than the rule (WB#3)? If a change fails one, it is friction: fix it before shipping, as a backend change that fails an invariant is a bug before it ships.

## The save is a full replace

A narrative edit resends the stored basket and segments. Sending empty lists would wipe the authored chain, so the editor never does (`docs/WORKBENCH.md`, "Create and edit"). Value-chain edits on the scored view go through the pure `chainOps` transforms, which mirror `useChainDraft` and never edit it.

## State, links and types

- Server state goes through TanStack Query, never ad-hoc fetch. TypeScript runs in strict mode. No browser storage for app state.
- A data-fed link goes through `ExternalLink` (`src/components/ExternalLink.tsx`), never a bare `<a href={...}>`; an EDGAR label only through `isSecLink`. `src/components/__tests__/externalLinkGuard.test.ts` enforces both; the rules are in `docs/SECURITY.md`.
- `src/api/types.gen.ts` is generated from `backend/openapi.json` by `npm run gen:api`. Never hand-edit it; regenerate it in the same PR as the backend change.
- UI copy is American English. A dashboard follows `docs/DASHBOARDS.md` and its own `docs/dashboards/<slug>.md`.

## Commands

From `frontend/`: `npm ci` (first time), `npm run dev` (Vite on :5173, proxies `/api` to 127.0.0.1:8000), `npm test` (vitest), `npm run build` (typecheck, then bundle), `npm run gen:api`.
