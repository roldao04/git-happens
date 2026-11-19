import { render, type RenderResult } from "@testing-library/react";
import type { ReactNode } from "react";
import type { HomeApi } from "../api/client";
import { MockHomeApi } from "../api/mock/MockHomeApi";
import { HomeProvider } from "../state/HomeProvider";

/** Renders inside a HomeProvider over a mock home with no delays. */
export function renderWithHome(ui: ReactNode, api: HomeApi = new MockHomeApi("demo", 0)): RenderResult & { api: HomeApi } {
  return { api, ...render(<HomeProvider api={api} pollMs={0}>{ui}</HomeProvider>) };
}
