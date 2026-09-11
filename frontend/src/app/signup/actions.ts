"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import type { AuthFormState } from "@/app/login/actions";
import {
  firstError,
  validateDisplayName,
  validateEmail,
  validatePassword,
} from "@/lib/auth/credentials";
import { DEFAULT_AUTHENTICATED_PATH } from "@/lib/auth/routes";
import { createClient } from "@/lib/supabase/server";

export async function signUp(
  _prevState: AuthFormState,
  formData: FormData,
): Promise<AuthFormState> {
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");
  const displayName = String(formData.get("display_name") ?? "");

  const invalid = firstError(
    validateDisplayName(displayName),
    validateEmail(email),
    validatePassword(password),
  );
  if (invalid) return { error: invalid };

  const supabase = await createClient();
  const { data, error } = await supabase.auth.signUp({
    email: email.trim(),
    password,
    options: {
      // Read by the on_auth_user_created trigger, which names the profile and
      // the default workspace from it. Anything stored here is user-supplied
      // and must be treated as untrusted by whatever reads it.
      data: { display_name: displayName.trim() },
    },
  });

  if (error) return { error: error.message };

  // With email confirmation enabled, signUp succeeds without a session: the
  // account exists but cannot be used until the link is clicked. Redirecting to
  // the dashboard here would bounce straight back to /login.
  if (!data.session) {
    return {
      error: null,
      notice: "Check your email to confirm your account, then sign in.",
    };
  }

  revalidatePath("/", "layout");
  redirect(DEFAULT_AUTHENTICATED_PATH);
}
