export type NavItem = {
  readonly href: string;
  readonly label: string;
};

export const navItems: readonly NavItem[] = [
  { href: "/", label: "Overview" },
  { href: "/dashboard", label: "Workspaces" },
] as const;
