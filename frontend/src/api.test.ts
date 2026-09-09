import { describe, expect, it } from "vitest";
import { ApiError, errorMessage } from "./api";

describe("API error mapping", () => {
  it("maps a domain error and keeps its trace identifier", () => {
    const message = errorMessage(new ApiError("HANDOFF_EXPIRED", "expired", false, "trace-42"));
    expect(message).toContain("交接已超时");
    expect(message).toContain("trace-42");
  });

  it("gives an actionable retry message for a network interruption", () => {
    expect(errorMessage(new Error("offline"))).toContain("输入已保留");
  });

  it("maps the rejection race errors for both adjudication outcomes", () => {
    expect(errorMessage(new ApiError("HANDOFF_REJECTED", "x", false))).toContain("已被夜班拒收");
    expect(errorMessage(new ApiError("HANDOFF_ALREADY_RECEIVED", "x", false))).toContain(
      "已确认接收",
    );
  });
});
