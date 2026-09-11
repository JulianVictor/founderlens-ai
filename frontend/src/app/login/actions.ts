"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { firstError, validateEmail, validatePassword } from "@/lib/auth/credentials";
import { safeRedirectPath } from "@/lib/auth/routes";
import { createClient } from "@/lib/supabase/server";

export type AuthFormState = { error: string | null; notice?: string | null };

export async function login(
  _prevState: AuthFormState,
  formData: FormData,
): Promise<AuthFormState> {
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");
  const next = safeRedirectPath(formData.get("next")?.toString());

  const invalid = firstError(validateEmail(email), validatePassword(password));
  if (invalid) return { error: invalid };

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({
    email: email.trim(),
    password,
  });

  if (error) {
    // Supabase returns a single "Invalid login credentials" for both a bad
    // password and an unknown address, which is what we want: distinguishing
    // them would turn this form into an account-enumeration oracle.
    return { error: error.message };
  }

  // The layout renders signed-in state, so its cache has to be dropped before
  // navigating or the header would still show the signed-out view.
  revalidatePath("/", "layout");
  redirect(next);
}

export async function signOut(): Promise<void> {
  const supabase = await createClient();
  await supabase.auth.signOut();
  revalidatePath("/", "layout");
  redirect("/login");
}
