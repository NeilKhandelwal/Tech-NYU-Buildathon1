import { scryptSync, timingSafeEqual } from "node:crypto";

// Password hashes for STAFF_USERS, in the format "scrypt$<salt hex>$<key hex>" so a hash
// carries its own salt. Node's crypto does this well enough that a bcrypt dependency
// would buy nothing. The matching hash *generator* is scripts/hash.mjs — a plain node
// script so `npm run hash-password` needs no TypeScript step. The four scrypt parameters
// are the one thing duplicated between the two files; they must stay in step.
const N = 16384;
const r = 8;
const p = 1;
const KEYLEN = 32;

export function verify(plain: string, stored: string) {
  const [scheme, salt, key] = stored.split("$");
  if (scheme !== "scrypt" || !salt || !key) return false;
  const expected = Buffer.from(key, "hex");
  if (expected.length !== KEYLEN) return false;
  const actual = scryptSync(plain, Buffer.from(salt, "hex"), KEYLEN, { N, r, p });
  return timingSafeEqual(expected, actual);
}
