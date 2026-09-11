"use client";

import Link from "next/link";
import { useActionState } from "react";
import { useFormStatus } from "react-dom";

import type { AuthFormState } from "@/app/login/actions";
import { MIN_PASSWORD_LENGTH } from "@/lib/auth/credentials";

type Field = "display_name" | "email" | "password";

type AuthFormProps = {
  title: string;
  description: string;
  fields: readonly Field[];
  action: (state: AuthFormState, formData: FormData) => Promise<AuthFormState>;
  submitLabel: string;
  pendingLabel: string;
  /** Where sign-in should return the user to, carried through the form. */
  next?: string;
  footer: { prompt: string; href: string; label: string };
};

const INITIAL_STATE: AuthFormState = { error: null, notice: null };

function SubmitButton({ label, pendingLabel }: { label: string; pendingLabel: string }) {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      className="mt-2 rounded-md bg-foreground px-4 py-2.5 text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-50"
    >
      {pending ? pendingLabel : label}
    </button>
  );
}

export function AuthForm({
  title,
  description,
  fields,
  action,
  submitLabel,
  pendingLabel,
  next,
  footer,
}: AuthFormProps) {
  const [state, formAction] = useActionState(action, INITIAL_STATE);

  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="text-sm text-black/60 dark:text-white/60">{description}</p>
      </header>

      <form action={formAction} className="flex flex-col gap-4">
        {next ? <input type="hidden" name="next" value={next} /> : null}

        {fields.includes("display_name") ? (
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Name</span>
            <input
              name="display_name"
              type="text"
              required
              maxLength={100}
              autoComplete="name"
              className="rounded-md border border-black/15 bg-transparent px-3 py-2 outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
            />
          </label>
        ) : null}

        {fields.includes("email") ? (
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Email</span>
            <input
              name="email"
              type="email"
              required
              autoComplete="email"
              className="rounded-md border border-black/15 bg-transparent px-3 py-2 outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
            />
          </label>
        ) : null}

        {fields.includes("password") ? (
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Password</span>
            <input
              name="password"
              type="password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              autoComplete={
                submitLabel === "Create account" ? "new-password" : "current-password"
              }
              className="rounded-md border border-black/15 bg-transparent px-3 py-2 outline-none focus:border-black/40 dark:border-white/15 dark:focus:border-white/40"
            />
          </label>
        ) : null}

        {state.error ? (
          <p
            role="alert"
            className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-sm text-red-600 dark:text-red-400"
          >
            {state.error}
          </p>
        ) : null}

        {state.notice ? (
          <p
            role="status"
            className="rounded-md border border-black/15 px-3 py-2 text-sm dark:border-white/15"
          >
            {state.notice}
          </p>
        ) : null}

        <SubmitButton label={submitLabel} pendingLabel={pendingLabel} />
      </form>

      <p className="text-sm text-black/60 dark:text-white/60">
        {footer.prompt}{" "}
        <Link href={footer.href} className="font-medium underline underline-offset-4">
          {footer.label}
        </Link>
      </p>
    </div>
  );
}
