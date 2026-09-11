import { describe, expect, it } from "vitest";

import {
  MAX_DISPLAY_NAME_LENGTH,
  MIN_PASSWORD_LENGTH,
  firstError,
  validateDisplayName,
  validateEmail,
  validatePassword,
} from "@/lib/auth/credentials";

describe("validateEmail", () => {
  it("accepts an ordinary address", () => {
    expect(validateEmail("ada@example.com").ok).toBe(true);
  });

  it("ignores surrounding whitespace", () => {
    expect(validateEmail("  ada@example.com  ").ok).toBe(true);
  });

  it.each(["", "   ", "ada", "ada@", "@example.com", "ada@example", "a b@c.com"])(
    "rejects %j",
    (candidate) => {
      expect(validateEmail(candidate).ok).toBe(false);
    },
  );
});

describe("validatePassword", () => {
  it("accepts a password at the minimum length", () => {
    expect(validatePassword("a".repeat(MIN_PASSWORD_LENGTH)).ok).toBe(true);
  });

  it("rejects one character short", () => {
    const result = validatePassword("a".repeat(MIN_PASSWORD_LENGTH - 1));
    expect(result.ok).toBe(false);
  });

  it("rejects an empty password", () => {
    expect(validatePassword("").ok).toBe(false);
  });

  it("does not impose rules Supabase would accept", () => {
    // No complexity requirement: the form must never reject a password the
    // service itself would have allowed.
    expect(validatePassword("aaaaaaaa").ok).toBe(true);
  });
});

describe("validateDisplayName", () => {
  it("accepts a normal name", () => {
    expect(validateDisplayName("Ada Lovelace").ok).toBe(true);
  });

  it("rejects blank and whitespace-only names", () => {
    // profiles.display_name is NOT NULL and rejects blanks in the database too.
    expect(validateDisplayName("").ok).toBe(false);
    expect(validateDisplayName("   ").ok).toBe(false);
  });

  it("matches the column's length limit", () => {
    expect(validateDisplayName("a".repeat(MAX_DISPLAY_NAME_LENGTH)).ok).toBe(true);
    expect(validateDisplayName("a".repeat(MAX_DISPLAY_NAME_LENGTH + 1)).ok).toBe(
      false,
    );
  });
});

describe("firstError", () => {
  it("returns null when everything passes", () => {
    expect(firstError({ ok: true }, { ok: true })).toBeNull();
  });

  it("returns the first failure, so the user fixes one thing at a time", () => {
    expect(
      firstError({ ok: true }, { ok: false, error: "first" }, { ok: false, error: "second" }),
    ).toBe("first");
  });
});
