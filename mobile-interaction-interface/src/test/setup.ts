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
  // @ts-expect-error jsdom stand-in
  window.PointerEvent = PointerEventStandIn;
}

// jsdom has no scrollTo on elements.
if (!Element.prototype.scrollTo) Element.prototype.scrollTo = () => {};
