/**
 * Client-side credential validation.
 *
 * This exists to give immediate, specific feedback, not to enforce anything:
 * Supabase Auth is the authority on whether a password is acceptable, and the
 * checks here are deliberately no stricter than its defaults so that the form
 * never rejects something the service would have allowed.
 */

/** Supabase's own default minimum. Kept in step with the project's Auth settings. */
export const MIN_PASSWORD_LENGTH = 6;
export const MAX_DISPLAY_NAME_LENGTH = 100;

export type ValidationResult = { ok: true } | { ok: false; error: string };

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateEmail(email: string): ValidationResult {
  const trimmed = email.trim();
  if (!trimmed) return { ok: false, error: "Email is required." };
  if (!EMAIL_PATTERN.test(trimmed)) {
    return { ok: false, error: "Enter a valid email address." };
  }
  return { ok: true };
}

export function validatePassword(password: string): ValidationResult {
  if (!password) return { ok: false, error: "Password is required." };
  if (password.length < MIN_PASSWORD_LENGTH) {
    return {
      ok: false,
      error: `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`,
    };
  }
  return { ok: true };
}

export function validateDisplayName(displayName: string): ValidationResult {
  const trimmed = displayName.trim();
  if (!trimmed) return { ok: false, error: "Name is required." };
  if (trimmed.length > MAX_DISPLAY_NAME_LENGTH) {
    return {
      ok: false,
      error: `Name must be ${MAX_DISPLAY_NAME_LENGTH} characters or fewer.`,
    };
  }
  return { ok: true };
}

/** First failure among the given results, or `null` when all pass. */
export function firstError(...results: ValidationResult[]): string | null {
  for (const result of results) {
    if (!result.ok) return result.error;
  }
  return null;
}
