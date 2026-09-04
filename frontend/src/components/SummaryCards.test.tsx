import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SummaryCards } from "./SummaryCards";
import type { UsageSummary } from "../lib/api";

const base: UsageSummary = {
  total_requests: 12,
  total_tokens: 3400,
  total_prompt_tokens: 1000,
  total_completion_tokens: 2400,
  total_cost: 0.0123,
  cost_actual: 0,
  cost_estimated: 0.0123,
  latency_p50_ms: 800,
  latency_p95_ms: 1900,
  error_rate: 0,
  errors: 0,
  tokens_per_sec: 42,
  active_keys: 3,
};

describe("SummaryCards", () => {
  it("shows a loading skeleton when data is null", () => {
    const { container } = render(<SummaryCards data={null} loading />);
    expect(container.querySelectorAll(".animate-pulse").length).toBe(4);
  });

  it("renders totals and the estimated-only cost hint", () => {
    render(<SummaryCards data={base} loading={false} />);
    expect(screen.getByText("Total Requests")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText(/estimated from configured pricing/i)).toBeInTheDocument();
  });

  it("splits actual vs estimated cost when real spend exists", () => {
    render(
      <SummaryCards data={{ ...base, cost_actual: 0.004, cost_estimated: 0.008 }} loading={false} />,
    );
    expect(screen.getByText(/actual/i)).toBeInTheDocument();
    expect(screen.getByText(/est\./i)).toBeInTheDocument();
  });
});
