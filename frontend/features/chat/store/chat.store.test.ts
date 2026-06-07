import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore, createMessage } from "./chat.store";

const reset = () =>
  useChatStore.setState({
    messages: [],
    draft: "",
    webSearchAllowed: false,
    isLoading: false,
  });

describe("chat.store", () => {
  beforeEach(reset);

  it("addMessage appends with defaulted fields", () => {
    const m = createMessage({ role: "user", content: "hi" });
    useChatStore.getState().addMessage(m);
    const [stored] = useChatStore.getState().messages;
    expect(stored.content).toBe("hi");
    expect(stored.status).toBe("pending");
    expect(stored.steps).toEqual([]);
    expect(stored.sources).toEqual([]);
    expect(typeof stored.timestamp).toBe("number");
  });

  it("appendContent concatenates (streaming-equivalent)", () => {
    const m = createMessage({ role: "assistant", content: "" });
    const { addMessage, appendContent } = useChatStore.getState();
    addMessage(m);
    appendContent(m.id, "Hel");
    appendContent(m.id, "lo");
    expect(useChatStore.getState().messages[0].content).toBe("Hello");
  });

  it("pushStep replaces a step with the same label", () => {
    const m = createMessage({ role: "assistant", content: "" });
    const { addMessage, pushStep } = useChatStore.getState();
    addMessage(m);
    pushStep(m.id, { label: "routing", state: "active" });
    pushStep(m.id, { label: "routing", state: "complete" });
    const steps = useChatStore.getState().messages[0].steps;
    expect(steps).toHaveLength(1);
    expect(steps[0].state).toBe("complete");
  });

  it("setSources + finalize produce a done message", () => {
    const m = createMessage({ role: "assistant", content: "answer" });
    const { addMessage, setSources, finalize } = useChatStore.getState();
    addMessage(m);
    setSources(m.id, [{ id: "s0", title: "Source chunk 1" }]);
    finalize(m.id);
    const msg = useChatStore.getState().messages[0];
    expect(msg.sources).toHaveLength(1);
    expect(msg.status).toBe("done");
  });

  it("reset clears messages and loading", () => {
    const { addMessage, setLoading, reset: r } = useChatStore.getState();
    addMessage(createMessage({ role: "user", content: "x" }));
    setLoading(true);
    r();
    expect(useChatStore.getState().messages).toEqual([]);
    expect(useChatStore.getState().isLoading).toBe(false);
  });
});
