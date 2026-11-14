import { afterEach, describe, expect, it, vi } from "vitest";

import {
  authUnavailableResponse,
  checkAuthAgreement,
  resetMismatchLog,
} from "../authAgreement";

const API = "http://api.test";

describe("authUnavailableResponse", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the backend's localised refusal as a 503 carrying help", async () => {
    const refusal = {
      detail: "साइन इन उपलब्ध नहीं",
      escalation_footer: "footer",
      crisis_response: null,
      helplines: [{ number: "15100", label: "NALSA" }],
    };
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      Response.json(refusal, { status: 503 }),
    );

    const response = await authUnavailableResponse("hi", {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      apiUrl: API,
    });

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual(refusal);
    expect(String(fetchImpl.mock.calls[0][0])).toContain("language=hi");
  });

  it.each(["en", "hi"])(
    "falls back to bundled helplines (%s) when the backend cannot answer",
    async (language) => {
      const fetchImpl = vi.fn(async () => {
        throw new Error("down");
      });

      const response = await authUnavailableResponse(language, {
        fetchImpl: fetchImpl as unknown as typeof fetch,
        apiUrl: API,
      });
      const body = await response.json();

      expect(response.status).toBe(503);
      expect(body.escalation_footer).toBeTruthy();
      expect(body.helplines.some((h: { number: string }) => h.number === "15100")).toBe(true);
    },
  );

  it("falls back when the backend body is not a refusal", async () => {
    const fetchImpl = vi.fn(async () => new Response("<html>502</html>", { status: 502 }));

    const response = await authUnavailableResponse("en", {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      apiUrl: API,
    });

    expect(response.status).toBe(503);
    expect((await response.json()).helplines.length).toBeGreaterThan(0);
  });
});

describe("checkAuthAgreement", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("treats a malformed or non-2xx status response as unknown", async () => {
    resetMismatchLog();
    for (const reply of [
      new Response("nope", { status: 500 }),
      Response.json({ auth_enabled: "yes" }),
    ]) {
      const result = await checkAuthAgreement({
        fetchImpl: (async () => reply) as unknown as typeof fetch,
        frontendEnabled: true,
        apiUrl: API,
      });
      expect(result.agrees).toBeNull();
    }
  });
});
