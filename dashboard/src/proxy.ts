import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { authEnabled } from "@/lib/staff";

// Every route is staff-only. Unauthenticated visitors go to /login with the path they
// wanted, so signing in lands them where they were headed. Next 16 renamed middleware to
// proxy (node_modules/next/dist/docs/.../proxy.md); it runs on the Node runtime, which is
// what lets this import the same auth config the API route uses.
export default auth((req) => {
  if (!authEnabled || req.auth) return NextResponse.next();
  const url = new URL("/login", req.nextUrl);
  url.searchParams.set("next", req.nextUrl.pathname + req.nextUrl.search);
  return NextResponse.redirect(url);
});

export const config = {
  // Everything except the login page, Auth.js's own endpoints, and static assets — a
  // redirected stylesheet would break the login page it redirected to.
  matcher: ["/((?!login|api/auth|_next/static|_next/image|favicon.ico).*)"],
};
