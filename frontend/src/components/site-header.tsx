import Link from "next/link";

import { navItems } from "@/lib/navigation";

export function SiteHeader() {
  return (
    <header className="border-b border-black/10 dark:border-white/10">
      <nav className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <Link href="/" className="font-mono text-sm font-semibold tracking-tight">
          FounderLens<span className="text-black/40 dark:text-white/40"> AI</span>
        </Link>
        <ul className="flex items-center gap-6 text-sm">
          {navItems.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                className="text-black/60 transition-colors hover:text-black dark:text-white/60 dark:hover:text-white"
              >
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </header>
  );
}
