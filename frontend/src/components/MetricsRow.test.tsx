import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MetricsRow } from "./MetricsRow";
import type { UsageSummary } from "../lib/api";

const summary: UsageSummary = {
  total_requests: 5,
  total_tokens: 100,
  total_prompt_tokens: 40,
  total_completion_tokens: 60,
  total_cost: 0.001,
  cost_actual: 0,
  cost_estimated: 0.001,
  latency_p50_ms: 812,
  latency_p95_ms: 1930,
  error_rate: 0.2,
  errors: 1,
  tokens_per_sec: 37.5,
  active_keys: 2,
};

describe("MetricsRow", () => {
  it("renders latency percentiles, error rate and throughput", () => {
    render(<MetricsRow data={summary} loading={false} />);
    expect(screen.getByText("Latency p50")).toBeInTheDocument();
    expect(screen.getByText("812 ms")).toBeInTheDocument();
    expect(screen.getByText("1930 ms")).toBeInTheDocument();
    expect(screen.getByText("20.0%")).toBeInTheDocument();
    expect(screen.getByText("37.5")).toBeInTheDocument();
  });
});
