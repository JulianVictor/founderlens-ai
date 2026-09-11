import type { Metadata } from "next";

import { login } from "@/app/login/actions";
import { AuthForm } from "@/components/auth/auth-form";
import { safeRedirectPath } from "@/lib/auth/routes";

export const metadata: Metadata = {
  title: "Sign in — FounderLens AI",
};

export default async function LoginPage({
  searchParams,
}: PageProps<"/login">) {
  const params = await searchParams;
  const next = safeRedirectPath(
    typeof params.next === "string" ? params.next : undefined,
  );

  return (
    <AuthForm
      title="Sign in"
      description="Continue to your research workspaces."
      fields={["email", "password"]}
      action={login}
      submitLabel="Sign in"
      pendingLabel="Signing in…"
      next={next}
      footer={{
        prompt: "No account yet?",
        href: "/signup",
        label: "Create one",
      }}
    />
  );
}
