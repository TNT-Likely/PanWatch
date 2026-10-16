import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  qlibApi,
  type QlibMarket,
  type QlibStrategyCatalog,
  type QlibModel,
} from "@panwatch/api";
import { Button } from "@panwatch/base-ui/components/ui/button";
import { useConfirm } from "@panwatch/base-ui/components/ui/confirm-dialog";
import { planError } from "./paper-plan-dialog";

const percent = (v: number | null | undefined) =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;

export default function RuleStrategies({
  market,
  onChange,
}: {
  market: QlibMarket;
  onChange: () => void;
}) {
  const { t } = useTranslation("qlib");
  const translate = t as unknown as (
    key: string,
    args?: Record<string, unknown>,
  ) => string;
  const tr = (key: string, args?: Record<string, unknown>) =>
    translate("ruleStrategies." + key, args);
  const confirmAction = useConfirm();
  const [data, setData] = useState<QlibStrategyCatalog | null>(null);
  const [selected, setSelected] = useState("trend_breakout");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const observed = useRef<string | null>(null);
  const reload = useCallback(async () => {
    try {
      const next = await qlibApi.strategies(market);
      if (observed.current === null) {
        const current = next.items.find(
          (item) => item.model?.id === next.active_id,
        );
        if (current) setSelected(current.algorithm);
      }
      setData(next);
      const identity = `${next.active_id}:${next.selection_id}`;
      if (observed.current !== null && observed.current !== identity)
        onChange();
      observed.current = identity;
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    }
  }, [market, onChange]);
  useEffect(() => {
    observed.current = null;
    setData(null);
    void reload();
    const timer = window.setInterval(() => void reload(), 5000);
    return () => window.clearInterval(timer);
  }, [reload]);
  const model = data?.items.find((i) => i.algorithm === selected)?.model;
  const running = data?.jobs.some(
    (j) => j.algorithm && ["queued", "running"].includes(j.status),
  );
  const latestFailure = data?.jobs.find(
    (j) =>
      j.algorithm === selected &&
      j.status === "failed" &&
      (!model || (j.created_at && j.created_at > model.created_at)),
  );
  const action = async (callback: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await callback();
      await reload();
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card mb-4 space-y-3 p-4" data-testid="rule-strategies">
      <div className="flex flex-wrap justify-between gap-2">
        <h2 className="font-semibold text-sm">{tr("title")}</h2>
        <p className="text-xs text-muted-foreground">{tr("journey")}</p>
      </div>
      <p className="text-xs text-muted-foreground">{tr("note")}</p>
      <div
        className="grid gap-2 md:grid-cols-3"
        role="group"
        aria-label={tr("choose")}
      >
        {data?.items.map((item) => (
          <Button
            key={item.algorithm}
            variant={selected === item.algorithm ? "default" : "outline"}
            aria-pressed={selected === item.algorithm}
            className="h-auto min-h-12 whitespace-normal text-left justify-start"
            onClick={() => setSelected(item.algorithm)}
          >
            <span>
              {tr("names." + item.algorithm)}
              {data.active_id === item.model?.id ? ` · ${tr("current")}` : ""}
            </span>
          </Button>
        ))}
      </div>
      <p className="text-sm">{tr("rules." + selected)}</p>
      <p className="text-xs text-muted-foreground">{tr("exit")}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={busy || running || !data}
          onClick={() =>
            void action(() =>
              qlibApi.prepareStrategy(
                selected as Parameters<typeof qlibApi.prepareStrategy>[0],
                market,
              ),
            )
          }
        >
          {tr(model ? "rerun" : "prepare")}
        </Button>
        {model && (
          <Button
            size="sm"
            disabled={busy || running || data?.active_id === model.id}
            onClick={async () => {
              if (await confirmAction(tr("activateConfirm")))
                await action(() =>
                  qlibApi.activateStrategy(model.id, data!.revision),
                );
            }}
          >
            {tr(data?.active_id === model.id ? "current" : "activate")}
          </Button>
        )}
        {model && data?.active_id === model.id && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void action(() => qlibApi.refreshStrategy(model.id))}
          >
            {tr("refresh")}
          </Button>
        )}
      </div>
      {running && (
        <p role="status" className="text-sm">
          {tr("running")}
        </p>
      )}
      {(error || latestFailure) && (
        <p role="alert" className="text-sm text-destructive break-words">
          {planError(error || latestFailure!.error_code, translate)}
        </p>
      )}
      {model && (
        <details open={data?.active_id !== model.id} className="text-sm">
          <summary className="cursor-pointer text-primary">
            {tr("details")}
          </summary>
          <RuleReplay model={model} />
        </details>
      )}
    </section>
  );
}

function RuleReplay({ model }: { model: QlibModel }) {
  const { t } = useTranslation("qlib");
  const tr = (key: string, args?: Record<string, unknown>) =>
    (t as unknown as (k: string, a?: Record<string, unknown>) => string)(
      "ruleStrategies." + key,
      args,
    );
  const bt = model.metrics.backtest;
  const curve = bt.curve ?? [];
  const values = curve.map((p) => p.equity);
  const low = Math.min(1, ...values),
    high = Math.max(1, ...values);
  const points = curve
    .map(
      (p, i) =>
        `${10 + (i / Math.max(1, curve.length - 1)) * 580},${150 - ((p.equity - low) / Math.max(0.001, high - low)) * 130}`,
    )
    .join(" ");
  return (
    <div className="rounded-md border p-3 space-y-3">
      <p className="text-xs text-muted-foreground">
        {tr("sample", {
          from: model.segments.test?.[0],
          to: model.segments.test?.[1],
        })}
      </p>
      <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {[
          [tr("return"), percent(bt.return)],
          [tr("drawdown"), percent(bt.max_drawdown)],
          [tr("baseline"), percent(model.baseline.backtest.return)],
          [tr("fills"), bt.fill_count ?? "—"],
        ].map(([k, v]) => (
          <p key={k}>
            <span className="block text-xs text-muted-foreground">{k}</span>
            {v}
          </p>
        ))}
      </div>
      <svg
        viewBox="0 0 600 170"
        role="img"
        aria-label={tr("curve")}
        className="w-full max-h-48"
      >
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-primary"
        />
      </svg>
      <p className="text-xs text-muted-foreground">
        {tr("curveRange", { low: low.toFixed(3), high: high.toFixed(3) })}
      </p>
      <details className="text-xs space-y-2">
        <summary className="cursor-pointer text-primary">
          {tr("details")}
        </summary>
        <p className="text-muted-foreground">{tr("limitations")}</p>
        <div className="grid grid-cols-3 gap-2">
          {model.rolling.map((fold, i) => (
            <p key={i}>
              {tr("window", { n: i + 1 })}
              <br />
              {percent(fold.metrics.backtest.return)}
            </p>
          ))}
        </div>
        <p>{tr("fillsNote")}</p>
        <div className="scrollbar max-h-60 overflow-auto">
          <table className="w-full min-w-[480px] text-left">
            <thead>
              <tr>
                {["date", "symbol", "side", "quantity", "price", "fee"].map(
                  (k) => (
                    <th key={k} className="p-1">
                      {tr(k)}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {bt.fills?.slice(-30).map((fill, i) => (
                <tr key={i}>
                  {[
                    fill.date,
                    fill.symbol,
                    tr(fill.side),
                    fill.quantity,
                    fill.price.toFixed(2),
                    fill.fee.toFixed(2),
                  ].map((v, j) => (
                    <td key={j} className="p-1">
                      {v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
