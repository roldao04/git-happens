import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

// jsdom has no PointerEvent: the chat list's swipe-to-delete listens for pointer events.
if (typeof window.PointerEvent === "undefined") {
  class PointerEventStandIn extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
    }
  }
  Object.defineProperty(window, "PointerEvent", { value: PointerEventStandIn, configurable: true, writable: true });
}

// jsdom has no scrollTo on elements.
if (!Element.prototype.scrollTo) Element.prototype.scrollTo = () => {};
