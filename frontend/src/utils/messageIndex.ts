/** O(1) normal stream updates. Only late insertions and history need ordering. */
export interface IndexedMessage { id: string; created_at?: string }
const timestamp = (item: IndexedMessage) => Date.parse(item.created_at || "") || 0;

export function upsertMessage<T extends IndexedMessage>(rows: T[], positions: Map<string, number>, message: T): void {
  const found = positions.get(message.id);
  if (found !== undefined) { rows[found] = message; return; }
  if (!rows.length || timestamp(message) >= timestamp(rows[rows.length - 1])) {
    positions.set(message.id, rows.length); rows.push(message); return;
  }
  let lo = 0;
  let hi = rows.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (timestamp(rows[mid]) <= timestamp(message)) lo = mid + 1;
    else hi = mid;
  }
  rows.splice(lo, 0, message);
  for (let i = lo; i < rows.length; i++) positions.set(rows[i].id, i);
}

export function mergeHistory<T extends IndexedMessage>(cached: T[], history: T[], live: T[] = []): T[] {
  const byId = new Map<string, T>();
  // Only deltas received since this request began outrank its HTTP snapshot.
  for (const item of [...cached, ...history, ...live]) if (item.id) byId.set(item.id, item);
  return [...byId.values()].sort((a, b) => timestamp(a) - timestamp(b));
}
