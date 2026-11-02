/** Stable opaque DOM identifiers. Source labels and IDs never cross into advertised target IDs.
 * The four independent 32-bit lanes make accidental collisions unlikely; observe() also rejects
 * every duplicate before publishing a view, so a collision can never select an ambiguous target.
 */
export function targetId(kind: string, ...sourceKeys: readonly string[]): string {
  const source = JSON.stringify([kind, sourceKeys]);
  const lanes = [0x811c9dc5, 0x9e3779b9, 0x85ebca6b, 0xc2b2ae35];
  const primes = [0x01000193, 0x85ebca77, 0xc2b2ae3d, 0x27d4eb2f];
  for (let i = 0; i < source.length; i++) {
    const code = source.charCodeAt(i);
    for (let lane = 0; lane < lanes.length; lane++) {
      lanes[lane] = Math.imul(lanes[lane] ^ code, primes[lane]);
      lanes[lane] ^= lanes[lane] >>> 13;
    }
  }
  return `target-${lanes.map((lane) => (lane >>> 0).toString(16).padStart(8, "0")).join("")}`;
}
