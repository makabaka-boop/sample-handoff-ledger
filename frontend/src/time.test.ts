import { describe, expect, it } from "vitest";
import { createServerCountdown, formatDuration } from "./time";

describe("server countdown", () => {
  it("uses monotonic elapsed time rather than the client wall clock", () => {
    const countdown = createServerCountdown(
      "2026-09-09T20:00:00Z",
      "2026-09-09T20:01:00Z",
      1000,
    );
    expect(countdown.remainingSeconds(1000)).toBe(60);
    expect(countdown.remainingSeconds(16500)).toBe(45);
  });

  it("never displays a negative duration", () => {
    const countdown = createServerCountdown(
      "2026-09-09T20:00:00Z",
      "2026-09-09T20:00:01Z",
      0,
    );
    expect(countdown.remainingSeconds(4000)).toBe(0);
    expect(formatDuration(-1)).toBe("00:00");
  });
});
