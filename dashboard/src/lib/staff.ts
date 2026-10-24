// Staff accounts live in the environment, not a database: STAFF_USERS is a comma-separated
// list of "name:hash" pairs, where hash is what `npm run hash-password` prints. There are a
// handful of staff on one deployment; a users table can wait until there are not.
export type Staff = { name: string; hash: string };

export const STAFF: Staff[] = (process.env.STAFF_USERS ?? "")
  .split(",")
  .map((entry) => entry.trim())
  .filter(Boolean)
  .map((entry) => {
    const i = entry.indexOf(":"); // the hash itself contains "$" but never ":"
    return { name: entry.slice(0, i), hash: entry.slice(i + 1) };
  })
  .filter((s) => s.name && s.hash);

// No staff configured means no login: the proxy passes everything through and Nav shows
// nobody. That keeps `npm run dev` and `docker compose up` working on a fresh clone.
export const authEnabled = STAFF.length > 0;

// Warn once when the server starts, not once per build worker: `next build` imports this
// module in every worker, and eight identical lines in CI is noise, not a warning.
if (!authEnabled && process.env.NEXT_PHASE !== "phase-production-build") {
  console.warn("[auth] STAFF_USERS is unset — dashboard auth is DISABLED (dev only)");
}
