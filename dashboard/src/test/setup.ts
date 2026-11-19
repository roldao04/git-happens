import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

// jsdom has no PointerEvent, so fireEvent.pointer* would drop clientX
if (typeof globalThis.PointerEvent === "undefined") {
  class PointerEventStandIn extends MouseEvent {
    readonly pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
    }
  }
  Object.defineProperty(globalThis, "PointerEvent", { value: PointerEventStandIn, writable: true, configurable: true });
}
