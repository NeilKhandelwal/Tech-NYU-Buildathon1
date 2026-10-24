import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import Nav from "@/components/Nav";
import { auth } from "@/auth";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "EffiGov · Case Desk",
  description: "EffiGov case dashboard",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Nav needs the signed-in name; session is null when auth is off or on /login.
  const session = await auth();
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <Nav name={session?.user?.name ?? null} />
        {children}
      </body>
    </html>
  );
}
