import { describe, expect, it } from "vitest";

import {
  DEFAULT_AUTHENTICATED_PATH,
  isAuthRoute,
  isProtectedPath,
  redirectTargetForAuthenticatedUser,
  safeRedirectPath,
} from "@/lib/auth/routes";

describe("isProtectedPath", () => {
  it("protects the dashboard and everything under it", () => {
    expect(isProtectedPath("/dashboard")).toBe(true);
    expect(isProtectedPath("/dashboard/workspace-1")).toBe(true);
  });

  it("leaves public routes alone", () => {
    expect(isProtectedPath("/")).toBe(false);
    expect(isProtectedPath("/login")).toBe(false);
    expect(isProtectedPath("/signup")).toBe(false);
  });

  it("does not protect a path that merely starts with the same characters", () => {
    // "/dashboards-public" must not be treated as inside "/dashboard".
    expect(isProtectedPath("/dashboards-public")).toBe(false);
  });
});

describe("redirectTargetForAuthenticatedUser", () => {
  it("moves a signed-in user off the auth pages", () => {
    expect(redirectTargetForAuthenticatedUser("/login")).toBe(
      DEFAULT_AUTHENTICATED_PATH,
    );
    expect(redirectTargetForAuthenticatedUser("/signup")).toBe(
      DEFAULT_AUTHENTICATED_PATH,
    );
  });

  it("leaves every other route alone", () => {
    expect(redirectTargetForAuthenticatedUser("/")).toBeNull();
    expect(redirectTargetForAuthenticatedUser("/dashboard")).toBeNull();
  });
});

describe("isAuthRoute", () => {
  it("matches exactly, not by prefix", () => {
    expect(isAuthRoute("/login")).toBe(true);
    expect(isAuthRoute("/login/extra")).toBe(false);
  });
});

describe("safeRedirectPath", () => {
  it("accepts a same-site absolute path", () => {
    expect(safeRedirectPath("/dashboard/abc")).toBe("/dashboard/abc");
  });

  it("falls back when absent", () => {
    expect(safeRedirectPath(null)).toBe(DEFAULT_AUTHENTICATED_PATH);
    expect(safeRedirectPath(undefined)).toBe(DEFAULT_AUTHENTICATED_PATH);
    expect(safeRedirectPath("")).toBe(DEFAULT_AUTHENTICATED_PATH);
  });

  it.each([
    ["https://evil.example", "absolute url"],
    ["//evil.example", "protocol-relative url"],
    ["/\\evil.example", "backslash-escaped url"],
    ["evil", "relative path"],
  ])("rejects %s (%s)", (candidate) => {
    // An unchecked ?next= is an open redirect: it borrows this site's
    // credibility to send a user somewhere else.
    expect(safeRedirectPath(candidate)).toBe(DEFAULT_AUTHENTICATED_PATH);
  });

  it("refuses to bounce a signed-in user back to an auth page", () => {
    expect(safeRedirectPath("/login")).toBe(DEFAULT_AUTHENTICATED_PATH);
  });

  it("honours an explicit fallback", () => {
    expect(safeRedirectPath(null, "/")).toBe("/");
  });
});
