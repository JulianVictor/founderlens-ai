import { describe, expect, it } from "vitest";

import {
  canProcess,
  formatBytes,
  formatUploadedAt,
  hasWorkInFlight,
  statusLabel,
  uploadErrorMessage,
} from "@/lib/documents/format";

describe("statusLabel", () => {
  it.each([
    ["uploaded", "Uploaded"],
    ["processing", "Processing"],
    ["ready", "Ready"],
    ["failed", "Failed"],
  ])("labels %s", (status, expected) => {
    expect(statusLabel(status)).toBe(expected);
  });

  it("falls back to the raw value for a status it does not know", () => {
    // The API gaining a status before the frontend ships support for it should
    // show that status, not a blank cell.
    expect(statusLabel("quarantined")).toBe("quarantined");
  });
});

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [1024, "1.0 KB"],
    [1536, "1.5 KB"],
    [1024 * 1024, "1.0 MB"],
    [25 * 1024 * 1024, "25 MB"],
  ])("formats %i bytes", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });

  it("drops the decimal once the number is large enough not to need it", () => {
    expect(formatBytes(245 * 1024)).toBe("245 KB");
  });

  it("does not invent a size for nonsense input", () => {
    expect(formatBytes(Number.NaN)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
  });
});

describe("formatUploadedAt", () => {
  const now = new Date("2026-03-01T12:00:00Z");

  it.each([
    ["2026-03-01T11:59:30Z", "just now"],
    ["2026-03-01T11:59:00Z", "1 minute ago"],
    ["2026-03-01T11:30:00Z", "30 minutes ago"],
    ["2026-03-01T11:00:00Z", "1 hour ago"],
    ["2026-03-01T09:00:00Z", "3 hours ago"],
    ["2026-02-28T12:00:00Z", "1 day ago"],
    ["2026-02-25T12:00:00Z", "4 days ago"],
  ])("renders %s as %s", (iso, expected) => {
    expect(formatUploadedAt(iso, now)).toBe(expected);
  });

  it("falls back to a date once an interval stops being useful", () => {
    const result = formatUploadedAt("2026-01-05T12:00:00Z", now);
    expect(result).not.toContain("ago");
    expect(result).toContain("2026");
  });

  it("does not render a future time when the client clock runs ahead", () => {
    // A browser a few seconds ahead of the server must not say "in 5 seconds".
    expect(formatUploadedAt("2026-03-01T12:00:05Z", now)).toBe("just now");
  });

  it("does not crash on an unparseable timestamp", () => {
    expect(formatUploadedAt("not-a-date", now)).toBe("—");
  });
});

describe("uploadErrorMessage", () => {
  it("explains the size limit", () => {
    expect(uploadErrorMessage(413)).toContain("too large");
  });

  it("names the accepted formats when the type is refused", () => {
    const message = uploadErrorMessage(415);
    expect(message).toContain("PDF");
    expect(message).toContain("Markdown");
  });

  it("explains a duplicate rather than reporting a generic failure", () => {
    expect(uploadErrorMessage(409)).toContain("already");
  });

  it("tells the user to sign in again when the session has gone", () => {
    expect(uploadErrorMessage(401)).toContain("session");
  });

  it("prefers the server's detail where the server has something to say", () => {
    expect(uploadErrorMessage(413, "Document exceeds the 25 MB limit")).toBe(
      "Document exceeds the 25 MB limit",
    );
  });

  it("does not surface a raw server detail for an unexpected status", () => {
    // No detail: the user gets an actionable sentence, not a stack trace.
    expect(uploadErrorMessage(500)).toBe("Upload failed. Please try again.");
  });

  it("always returns something to show", () => {
    for (const status of [400, 401, 404, 409, 413, 415, 418, 500, 502, 503]) {
      expect(uploadErrorMessage(status).length).toBeGreaterThan(0);
    }
  });
});

describe("canProcess", () => {
  it("offers processing for a freshly uploaded document", () => {
    expect(canProcess("uploaded")).toBe(true);
  });

  it("offers a retry after a failure", () => {
    // Failures are often transient, and the backend treats a retry as a
    // fresh attempt rather than refusing one.
    expect(canProcess("failed")).toBe(true);
  });

  it("does not offer it while a run is in flight", () => {
    expect(canProcess("processing")).toBe(false);
  });

  it("does not offer it for a document that is already done", () => {
    expect(canProcess("ready")).toBe(false);
  });

  it("does not offer it for a status it does not recognise", () => {
    // Fails closed: a status shipped by the API before the UI knows about it
    // must not get an action whose effect nobody has thought about.
    expect(canProcess("quarantined")).toBe(false);
  });
});

describe("hasWorkInFlight", () => {
  it("is false for an empty list", () => {
    expect(hasWorkInFlight([])).toBe(false);
  });

  it("is false when everything has settled", () => {
    expect(
      hasWorkInFlight([{ status: "ready" }, { status: "failed" }, { status: "uploaded" }]),
    ).toBe(false);
  });

  it("is true when any one document is processing", () => {
    expect(hasWorkInFlight([{ status: "ready" }, { status: "processing" }])).toBe(true);
  });

  it("stops being true once the last run finishes", () => {
    // What ends the polling loop: a timer that never stops is a battery and
    // a rate-limit problem.
    const before = [{ status: "processing" }];
    const after = [{ status: "ready" }];

    expect(hasWorkInFlight(before)).toBe(true);
    expect(hasWorkInFlight(after)).toBe(false);
  });
});
