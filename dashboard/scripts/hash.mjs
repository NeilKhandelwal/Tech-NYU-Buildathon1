// `npm run hash-password -- <plain>` prints one "name:hash" hash for STAFF_USERS.
// Standalone on purpose: no TypeScript step. Keep the scrypt parameters in step with
// src/lib/password.ts, which verifies what this writes.
import { randomBytes, scryptSync } from "node:crypto";

const plain = process.argv[2];
if (!plain) {
  console.error("usage: npm run hash-password -- <password>");
  process.exit(1);
}
const salt = randomBytes(16);
const key = scryptSync(plain, salt, 32, { N: 16384, r: 8, p: 1 });
console.log(`scrypt$${salt.toString("hex")}$${key.toString("hex")}`);
