import type { Metadata } from "next";

import { signUp } from "@/app/signup/actions";
import { AuthForm } from "@/components/auth/auth-form";

export const metadata: Metadata = {
  title: "Create account — FounderLens AI",
};

export default function SignupPage() {
  return (
    <AuthForm
      title="Create your account"
      description="You'll start with a workspace of your own."
      fields={["display_name", "email", "password"]}
      action={signUp}
      submitLabel="Create account"
      pendingLabel="Creating account…"
      footer={{
        prompt: "Already have an account?",
        href: "/login",
        label: "Sign in",
      }}
    />
  );
}
