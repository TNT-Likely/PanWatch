import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { QlibAssessment } from "@panwatch/api";
import { planError } from "./paper-plan-dialog";

const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;

export default function StrategySummary({
  assessment,
}: {
  assessment?: QlibAssessment;
}) {
  const { t } = useTranslation("qlib");
  const tr = (key: string, values?: Record<string, unknown>) =>
    (t as unknown as (k: string, v?: Record<string, unknown>) => string)(
      "strategy." + key,
      values,
    );
  const translate = t as unknown as (
    k: string,
    v?: Record<string, unknown>,
  ) => string;
  return (
    <section className="card mb-4 space-y-3 p-4" data-testid="strategy-summary">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">
            {assessment?.rule_algorithm
              ? translate("ruleStrategies.names." + assessment.rule_algorithm)
              : tr("title", {
                  days: assessment?.horizon ?? 5,
                  topk: assessment?.topk ?? 10,
                })}
          </h2>
          <p className="mt-1 text-sm font-medium">
            {tr(
              assessment?.eligible
                ? "eligible"
                : assessment?.experiment_eligible
                  ? "paperExperiment"
                  : "observationOnly",
            )}
          </p>
        </div>
        <Link to="/research" className="text-xs text-primary underline">
          {tr("report")}
        </Link>
      </div>
      <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        {[
          [tr("netReturn"), percent(assessment?.net_return)],
          [tr("drawdown"), percent(assessment?.max_drawdown)],
          [tr("days"), assessment?.test_days ?? "—"],
          [
            tr("alignment"),
            tr(assessment?.policy_aligned ? "aligned" : "notAligned"),
          ],
        ].map(([label, value]) => (
          <div key={label}>
            <p className="text-muted-foreground">{label}</p>
            <p className="mt-1 font-semibold">{value}</p>
          </div>
        ))}
      </div>
      {!!assessment?.reasons.length && (
        <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
          {assessment.reasons.slice(0, 2).map((reason) => (
            <li key={reason}>{planError(reason, translate)}</li>
          ))}
        </ul>
      )}
      <details className="space-y-2 text-xs">
        <summary className="cursor-pointer text-primary">{tr("rules")}</summary>
        {!!assessment?.reasons.slice(2).length && (
          <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
            {assessment.reasons.slice(2).map((reason) => (
              <li key={reason}>{planError(reason, translate)}</li>
            ))}
          </ul>
        )}
        <p>
          {assessment?.rule_algorithm
            ? translate("ruleStrategies.rules." + assessment.rule_algorithm)
            : tr("selection")}
        </p>
        <p>{tr("allocation")}</p>
        <p>{tr("exit")}</p>
        <p className="text-[11px] text-muted-foreground">{tr("limits")}</p>
      </details>
    </section>
  );
}
