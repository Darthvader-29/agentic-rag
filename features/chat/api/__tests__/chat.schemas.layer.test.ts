import { describe, it, expect } from "vitest";
import {
  SseLayerSchema,
  SseDoneSchema,
  SseDoneSourceSchema,
} from "@/features/chat/api/chat.schemas";
import {
  citationSchema,
  componentSpecSchema,
} from "@/features/chat/components/rich/component.schemas";

describe("SseLayerSchema (Phase 7 retrieval layer)", () => {
  it.each(["vector", "graph", "web", "memory"])("accepts %s", (layer) => {
    expect(SseLayerSchema.parse(layer)).toBe(layer);
  });

  it("is legacy-safe: absent ⇒ undefined", () => {
    expect(SseLayerSchema.parse(undefined)).toBeUndefined();
  });

  it("drops an unknown layer to undefined rather than throwing", () => {
    expect(SseLayerSchema.parse("quantum")).toBeUndefined();
  });
});

describe("SseDoneSchema with optional sources/layer", () => {
  it("parses a done event WITHOUT sources (pre-Phase-7 contract)", () => {
    const parsed = SseDoneSchema.parse({ answer: "hi", route: "RAG" });
    expect(parsed.sources).toBeUndefined();
  });

  it("parses done sources carrying a layer (passthrough keeps extra fields)", () => {
    const parsed = SseDoneSchema.parse({
      answer: "hi",
      route: "BOTH",
      sources: [
        { source_id: "c1", layer: "vector" },
        { url: "https://x", layer: "web" },
      ],
    });
    expect(parsed.sources).toHaveLength(2);
    expect(parsed.sources?.[0].layer).toBe("vector");
    expect(parsed.sources?.[1].layer).toBe("web");
  });

  it("degrades a malformed sources payload to undefined (never fails done)", () => {
    const parsed = SseDoneSchema.parse({
      answer: "hi",
      route: "RAG",
      sources: "not-an-array",
    });
    expect(parsed.sources).toBeUndefined();
    expect(parsed.answer).toBe("hi");
  });

  it("SseDoneSourceSchema drops an unknown layer to undefined", () => {
    const parsed = SseDoneSourceSchema.parse({ source_id: "x", layer: "nope" });
    expect(parsed.layer).toBeUndefined();
  });
});

describe("citation item layer (strict component schema)", () => {
  it("accepts a citation item with a valid layer", () => {
    const parsed = citationSchema.parse({
      type: "citation",
      items: [{ label: "doc.pdf", source_id: "c1", layer: "graph" }],
    });
    expect(parsed.items[0].layer).toBe("graph");
  });

  it("stays valid when items omit layer (legacy-safe)", () => {
    const parsed = citationSchema.parse({
      type: "citation",
      items: [{ label: "doc.pdf", source_id: "c1" }],
    });
    expect(parsed.items[0].layer).toBeUndefined();
  });

  it("drops an unknown layer to undefined without dropping the citation", () => {
    const parsed = citationSchema.parse({
      type: "citation",
      items: [{ label: "doc.pdf", layer: "telepathy" }],
    });
    expect(parsed.items[0].layer).toBeUndefined();
    expect(parsed.items[0].label).toBe("doc.pdf");
  });

  it("the discriminated union still dispatches citation correctly", () => {
    const parsed = componentSpecSchema.parse({
      type: "citation",
      items: [{ label: "x", layer: "memory" }],
    });
    expect(parsed.type).toBe("citation");
  });
});
