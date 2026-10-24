import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { verify } from "@/lib/password";
import { STAFF, authEnabled } from "@/lib/staff";

// Credentials only for now: staff are a short list in the environment (see lib/staff.ts).
// OAuth lands when there are provider keys — Phase 3 of docs/ROADMAP.md.
export const { handlers, auth, signIn, signOut } = NextAuth({
  // JWT, not a database session: the dashboard has no store of its own to put one in.
  session: { strategy: "jwt" },
  pages: { signIn: "/login" },
  // Set AUTH_SECRET in the environment. A throwaway keeps `npm run build` and the
  // auth-disabled dev path from failing on a missing secret; nothing signs in there.
  secret: process.env.AUTH_SECRET ?? "dev-only-unset-secret",
  trustHost: true, // self-hosted behind compose/Fly: there is no AUTH_URL to check against
  providers: [
    Credentials({
      credentials: { name: {}, password: {} },
      authorize({ name, password }) {
        if (!authEnabled || typeof name !== "string" || typeof password !== "string") return null;
        const staff = STAFF.find((s) => s.name === name);
        // verify() on a miss too, so a wrong name and a wrong password cost the same.
        const ok = verify(password, staff?.hash ?? "scrypt$00$00");
        return staff && ok ? { id: staff.name, name: staff.name } : null;
      },
    }),
  ],
});
