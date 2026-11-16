import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "../page";
import { readAuthAgreement } from "@/lib/api";
import { ANONYMOUS_UPLOAD_TTL_HOURS } from "@/lib/documents";
import { getStrings } from "@/lib/strings";

// Final review H3c: a proven frontend/backend mode disagreement is shown on
// the page, in both build modes, not only logged.
const mode = vi.hoisted(() => ({ enabled: false }));
vi.mock("@/lib/authMode", () => ({
  get AUTH_ENABLED() {
    return mode.enabled;
  },
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    readConversation: vi.fn(),
    readAuthAgreement: vi.fn(),
  };
});

// The sidebar's list request (Mode B, signed in): an empty list.
vi.mock("@/lib/sidebarConversations", () => ({
  fetchSidebarPage: vi.fn(async () => ({ kind: "page" as const, conversations: [], nextOffset: null })),
}));
const mockedAgreement = vi.mocked(readAuthAgreement);
const banner = () => getStrings("en").misconfiguredBanner(ANONYMOUS_UPLOAD_TTL_HOURS);

beforeEach(() => {
  mockedAgreement.mockReset();
  window.sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ signedIn: false, email: null })));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe.each([
  ["Mode A", false],
  ["Mode B", true],
])("%s", (_label, enabled) => {
  beforeEach(() => {
    mode.enabled = enabled;
  });

  it("shows the operator banner when the modes disagree", async () => {
    mockedAgreement.mockResolvedValue(false);
    render(<HomePage />);

    expect(await screen.findByText(banner())).toBeInTheDocument();
  });

  it.each([true, null])("shows no banner when agreement is %s", async (agrees) => {
    mockedAgreement.mockResolvedValue(agrees);
    render(<HomePage />);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockedAgreement).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(banner())).not.toBeInTheDocument();
  });
});
