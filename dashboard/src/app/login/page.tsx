import LoginForm from "./LoginForm";

export const metadata = { title: "Sign in · EffiGov" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const { next } = await searchParams;
  // Only same-site paths: a "next" of https://elsewhere/ would make this an open redirect.
  const to = typeof next === "string" && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  return (
    <main className="flex-1 flex items-start justify-center px-6 py-20">
      <LoginForm next={to} />
    </main>
  );
}
