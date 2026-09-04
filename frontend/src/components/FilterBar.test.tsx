import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FilterBar } from "./FilterBar";
import { defaultFilters } from "../lib/filters";

describe("FilterBar", () => {
  it("fires onChange with the selected range preset", async () => {
    const onChange = vi.fn();
    render(
      <FilterBar
        filters={defaultFilters}
        onChange={onChange}
        models={[{ provider: "openrouter", model: "m", input_per_1m: 0, output_per_1m: 0 }]}
        keys={[]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "7d" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ rangeDays: 7 }),
    );
  });
});
