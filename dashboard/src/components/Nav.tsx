"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import { setActor, useLiveRefresh } from "@/lib/api";

const NOOP = () => {};

const LINKS = [
  { href: "/", label: "Cases" },
  { href: "/calls", label: "Calls" },
  { href: "/call", label: "Start a call" },
];

// name is null when nobody is signed in — either the visitor is on /login, or STAFF_USERS
// is unset and auth is off (dev only). Either way Nav shows no identity and no sign-out.
export default function Nav({ name }: { name: string | null }) {
  const path = usePathname();
  // During render, not in an effect: the pages below fetch on mount, and those first
  // requests should already carry X-Actor.
  setActor(name);
  // The dot only needs the socket state, so no poll: pollMs 0 keeps Nav off the wire
  // while every page already refetches for itself. The socket itself is shared with the
  // page's own hook, so the dot costs no connection either.
  const live = useLiveRefresh(NOOP, { pollMs: 0 });

  return (
    <header className="bg-slate-900 text-white">
      <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-8">
        <Link href="/" className="flex items-baseline gap-2">
          <span className="font-semibold tracking-tight text-lg">EffiGov</span>
          <span className="text-slate-400 text-sm">Case Desk</span>
        </Link>
        <nav className="flex gap-1 text-sm">
          {LINKS.map((l) => {
            const active = l.href === "/" ? path === "/" || path.startsWith("/cases") : path === l.href || path.startsWith(l.href + "/");
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`px-3 py-1.5 rounded-md ${active ? "bg-slate-700 text-white" : "text-slate-300 hover:text-white hover:bg-slate-800"}`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
        <span className="ml-auto flex items-center gap-2 text-xs text-slate-300">
          <span className={`h-2 w-2 rounded-full ${live ? "bg-emerald-400" : "bg-amber-400"}`} />
          {live ? "Live" : "Polling"}
        </span>
        {name && (
          <span className="flex items-center gap-3 text-xs text-slate-300">
            <span className="text-slate-400">·</span>
            <span className="text-white">{name}</span>
            <button onClick={() => signOut({ redirectTo: "/login" })} className="hover:text-white underline-offset-2 hover:underline">
              Sign out
            </button>
          </span>
        )}
      </div>
    </header>
  );
}
