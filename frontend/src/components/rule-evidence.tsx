import { useTranslation } from "react-i18next";
import type { QlibRuleEvidence } from "@panwatch/api";

export default function RuleEvidence({
  evidence,
}: {
  evidence: QlibRuleEvidence;
}) {
  const { t } = useTranslation("qlib");
  const tr = (key: string) =>
    (t as unknown as (k: string) => string)("ruleStrategies." + key);
  return (
    <div className="mt-2 space-y-1 text-xs" data-testid="rule-evidence">
      <p className="font-medium">
        {tr(evidence.active ? "matched" : "notMatched")}
      </p>
      {evidence.issue ? (
        <p>{tr("inputIssue")}</p>
      ) : (
        <>
          <ul className="space-y-1">
            {Object.entries(evidence.conditions).map(([name, passed]) => (
              <li key={name}>
                {passed ? "✓" : "—"} {tr("conditions." + name)}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground">
            {tr("values")}: {((evidence.values.return20 ?? 0) * 100).toFixed(1)}
            % · {(evidence.values.volume_ratio ?? 0).toFixed(2)}×
          </p>
          <p className="text-muted-foreground">
            {tr("levels")}:{" "}
            {["close", "ma20", "ma60"]
              .map((k) => evidence.values[k]?.toFixed(2) ?? "—")
              .join(" / ")}
          </p>
        </>
      )}
    </div>
  );
}
