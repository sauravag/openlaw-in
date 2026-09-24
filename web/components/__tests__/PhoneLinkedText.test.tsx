import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PhoneLinkedText from "../PhoneLinkedText";

describe("PhoneLinkedText", () => {
  it("renders a known helpline number as a tap-to-call link", () => {
    render(<PhoneLinkedText text="Call 112 now." />);
    expect(screen.getByRole("link", { name: "112" })).toHaveAttribute(
      "href",
      "tel:112",
    );
  });

  it("links every helpline number in a longer sentence", () => {
    render(
      <PhoneLinkedText text="If you are in danger right now, call 112. The women's helpline is 181, and the police women's helpline is 1091." />,
    );
    expect(screen.getByRole("link", { name: "112" })).toHaveAttribute("href", "tel:112");
    expect(screen.getByRole("link", { name: "181" })).toHaveAttribute("href", "tel:181");
    expect(screen.getByRole("link", { name: "1091" })).toHaveAttribute(
      "href",
      "tel:1091",
    );
  });

  it("does not linkify a non-helpline number such as a year", () => {
    render(<PhoneLinkedText text="the Act of 1987 applies." />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("preserves the surrounding text", () => {
    render(<PhoneLinkedText text="Call 112 now." />);
    expect(screen.getByText(/Call/)).toBeInTheDocument();
    expect(screen.getByText(/now\./)).toBeInTheDocument();
  });
});
