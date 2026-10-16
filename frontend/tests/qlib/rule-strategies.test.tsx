import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { qlibApi, type QlibStrategyCatalog } from "@panwatch/api";
import { ConfirmProvider } from "@panwatch/base-ui/components/ui/confirm-dialog";
import RuleStrategies from "@/components/rule-strategies";
import RuleEvidence from "@/components/rule-evidence";
import i18n from "@/i18n";

vi.mock("@panwatch/api", async (original) => ({
  ...(await original<typeof import("@panwatch/api")>()),
  qlibApi: {
    strategies: vi.fn(),
    prepareStrategy: vi.fn(),
    activateStrategy: vi.fn(),
  },
}));
const catalog: QlibStrategyCatalog = {
  version: "ohlcv_rules_v1",
  market: "CN",
  revision: 0,
  active_id: null,
  selection_id: null,
  items: [
    { algorithm: "trend_breakout", model: null },
    { algorithm: "strength_continuation", model: null },
    { algorithm: "oversold_repair", model: null },
  ],
  jobs: [],
};
beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("zh-CN");
  vi.mocked(qlibApi.strategies).mockResolvedValue(catalog);
});
afterEach(cleanup);

it("offers understandable rules and replay without training or automatic authority", async () => {
  render(
    <ConfirmProvider
      labels={{ title: "确认", cancel: "取消", confirm: "确认" }}
    >
      <RuleStrategies market="CN" onChange={() => {}} />
    </ConfirmProvider>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "强势延续" }),
  );
  expect(screen.getByText(/收盘价 > 20日均线 > 60日均线/)).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "用已有行情回放" }));
  await waitFor(() =>
    expect(qlibApi.prepareStrategy).toHaveBeenCalledWith(
      "strength_continuation",
      "CN",
    ),
  );
  expect(qlibApi.activateStrategy).not.toHaveBeenCalled();
});

it("shows passed and missing selection conditions rather than a misleading score", () => {
  render(
    <RuleEvidence
      evidence={{
        active: false,
        score: 0,
        rule_version: "ohlcv_rules_v1",
        conditions: { trend: true, volume: false },
        values: {
          return20: 0.1,
          volume_ratio: 0.9,
          close: 10,
          ma20: 9,
          ma60: 8,
        },
        issue: "",
      }}
    />,
  );
  expect(screen.getByText(/未匹配/)).toBeTruthy();
  expect(screen.getByText(/✓ 价格与/)).toBeTruthy();
  expect(screen.getByText(/— 量比达到/)).toBeTruthy();
  expect(screen.getByText(/10.0% · 0.90×/)).toBeTruthy();
});
