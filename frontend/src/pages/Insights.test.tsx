import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const insights = vi.fn();
const investigate = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    insights: (...a: unknown[]) => insights(...a),
    investigate: (...a: unknown[]) => investigate(...a),
  },
}));

import { Insights } from "./Insights";

const ALERT = {
  id: "cost_spike:key:1",
  type: "cost_spike",
  severity: "critical",
  title: "Cost spike",
  detail: "Engineering usage is 205% above its 7-day daily average.",
  metric: "cost",
  current: 24.82,
  baseline: 8.14,
  pct_change: 205,
  scope: { kind: "key", key_id: 1, key_label: "Engineering" },
};

const INVESTIGATION = {
  alert_id: "cost_spike:key:1",
  metric: "cost",
  headline: { metric: "cost", baseline: 8.14, current: 24.82, pct_change: 205 },
  contributors: [
    { label: "Engineering key", kind: "key", pct: 78, detail: "+$12.40" },
    { label: "gpt-4o model", kind: "model", pct: 54, detail: "+$8.60" },
    { label: "Higher request volume", kind: "volume", pct: 31, detail: "40 vs 30/day" },
  ],
  summary: "The cost rose from $8.14 to $24.82 (+205%), driven mainly by the Engineering key.",
  related_query: { key_id: 1, start: "2026-09-03T00:00:00Z", end: "2026-09-04T00:00:00Z" },
  analyzed: ["key", "provider", "model", "tokens", "cost"],
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <Insights />
    </MemoryRouter>,
  );

describe("Insights", () => {
  beforeEach(() => {
    insights.mockResolvedValue({ alerts: [ALERT] });
    investigate.mockResolvedValue(INVESTIGATION);
  });

  it("renders alert cards from the API", async () => {
    renderPage();
    expect(await screen.findByText("Cost spike")).toBeInTheDocument();
    expect(screen.getByText(/205% above/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Investigate" })).toBeInTheDocument();
  });

  it("investigates an alert and shows contributors + summary", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Investigate" }));
    expect(await screen.findByText(/COST investigation/i)).toBeInTheDocument();
    expect(screen.getByText("+$12.40")).toBeInTheDocument(); // contributor detail
    expect(screen.getByText("78%")).toBeInTheDocument();
    expect(screen.getByText(/driven mainly by the Engineering key/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "View related requests" }),
    ).toBeInTheDocument();
    expect(investigate).toHaveBeenCalledWith("cost_spike:key:1");
  });
});
