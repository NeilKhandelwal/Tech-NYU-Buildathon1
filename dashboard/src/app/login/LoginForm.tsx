"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { signIn } from "next-auth/react";

export default function LoginForm({ next }: { next: string }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    // redirect: false so a bad password stays on this page with a message, rather than
    // bouncing through Auth.js's own error page.
    const res = await signIn("credentials", { name, password, redirect: false }).catch(() => null);
    setBusy(false);
    if (!res || res.error) {
      setError("That name and password did not match.");
      return;
    }
    router.push(next);
    router.refresh(); // the layout renders the signed-in name, so re-fetch it
  }

  return (
    <form onSubmit={submit} className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <h1 className="text-lg font-semibold tracking-tight">Sign in</h1>
      <p className="mt-1 mb-5 text-sm text-slate-500">Case Desk is staff only.</p>
      <label htmlFor="name" className="block text-xs text-slate-500 mb-1">
        Name
      </label>
      <input
        id="name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        autoComplete="username"
        autoFocus
        className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 mb-3"
      />
      <label htmlFor="password" className="block text-xs text-slate-500 mb-1">
        Password
      </label>
      <input
        id="password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="current-password"
        className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5"
      />
      {error && <p className="mt-3 text-sm text-red-700">{error}</p>}
      <button
        type="submit"
        disabled={busy || !name || !password}
        className="mt-4 w-full px-3 py-1.5 rounded-md bg-slate-900 text-white text-sm disabled:opacity-40"
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
