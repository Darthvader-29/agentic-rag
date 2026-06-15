import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CitationComponent } from "@/features/chat/components/rich/citation";
import type { CitationSpec } from "@/features/chat/components/rich/component.schemas";

describe("CitationComponent (→ M3 sources panel)", () => {
  it("shows a 'Referenced N' trigger with count = items.length", () => {
    const spec: CitationSpec = {
      type: "citation",
      items: [
        { label: "doc.pdf · p.4", source_id: "s1" },
        { label: "example.com", url: "https://example.com" },
      ],
    };
    render(<CitationComponent spec={spec} />);
    // The M3 panel renders a collapsible "Referenced 2 chunks" trigger.
    expect(screen.getByText(/Referenced 2 chunk/i)).toBeInTheDocument();
  });

  it("renders an item with a url as a safe external link (rel=noopener)", async () => {
    const user = userEvent.setup();
    const spec: CitationSpec = {
      type: "citation",
      items: [{ label: "example.com", url: "https://example.com/page" }],
    };
    render(<CitationComponent spec={spec} />);

    await user.click(screen.getByRole("button", { name: /toggle sources/i }));

    const link = screen.getByRole("link", { name: /example\.com/i });
    expect(link).toHaveAttribute("href", "https://example.com/page");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("renders an item with only source_id as a non-navigating card", async () => {
    const user = userEvent.setup();
    const spec: CitationSpec = {
      type: "citation",
      items: [{ label: "chunk_8c1f", source_id: "chunk_8c1f", snippet: "…" }],
    };
    render(<CitationComponent spec={spec} />);

    await user.click(screen.getByRole("button", { name: /toggle sources/i }));

    // The M3 panel renders an anchor with href="#" (no target) when there is no url.
    const card = screen.getByText("chunk_8c1f").closest("a");
    expect(card).not.toBeNull();
    expect(card).toHaveAttribute("href", "#");
    expect(card).not.toHaveAttribute("target");
  });

  it("renders a javascript: citation url as an inert (#) link, never executable (R06)", async () => {
    const user = userEvent.setup();
    // A javascript:/data: url passes z.string().url(), so it can reach the renderer; the panel
    // disarms it to an inert "#" href. Construct the spec directly to simulate that.
    const spec = {
      type: "citation",
      items: [{ label: "evil-link", url: "javascript:alert(1)" }],
    } as unknown as CitationSpec;
    render(<CitationComponent spec={spec} />);

    await user.click(screen.getByRole("button", { name: /toggle sources/i }));

    const card = screen.getByText("evil-link").closest("a");
    expect(card).toHaveAttribute("href", "#");
    expect(card).not.toHaveAttribute("target");
  });
});
