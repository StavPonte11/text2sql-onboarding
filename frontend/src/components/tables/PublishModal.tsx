import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CheckCircle2, XCircle, Rocket } from "lucide-react";
import { App } from "antd";
import { publishApi, enrichmentApi, questionsApi, evalApi } from "../../api/client";
import type { PublishError } from "../../types";

interface Props {
  tableId: string;
  onClose: () => void;
  onTrackProgress?: () => void;
}

export function PublishModal({ tableId, onClose, onTrackProgress }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [published, setPublished] = useState(false);
  const [blockingErrors, setBlockingErrors] = useState<PublishError[]>([]);

  const { data: enrichment } = useQuery({
    queryKey: ["enrichment", tableId],
    queryFn: () => enrichmentApi.getLatest(tableId),
    retry: false,
  });

  const { data: questions } = useQuery({
    queryKey: ["questions", tableId],
    queryFn: () => questionsApi.list(tableId),
  });

  const { data: runs } = useQuery({
    queryKey: ["eval-runs", tableId],
    queryFn: () => evalApi.listRuns(tableId),
  });

  const latestRun = runs?.[0];
  const hasPassingEval = (latestRun?.score ?? 0) >= 0.50;

  const checks = [
    {
      label: "Enrichment exists",
      pass: !!enrichment,
    },
    {
      label: "At least 1 golden question",
      pass: (questions?.length ?? 0) >= 1,
    },
    {
      label: "Contains Execution Accuracy ≥ 50%",
      pass: hasPassingEval,
    },
    {
      label: "Table description ≥ 20 chars",
      pass: (enrichment?.data?.table_description?.length ?? 0) >= 20,
    },
    {
      label: "All columns have descriptions",
      pass: enrichment?.data?.columns?.every((c: any) => c.description?.length >= 10) ?? false,
    },
  ];

  const publishMutation = useMutation({
    mutationFn: () => publishApi.publish(tableId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["table", tableId] });
      qc.invalidateQueries({ queryKey: ["tables"] });
      setPublished(true);
      message.success("Table published successfully");
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail;
      if (detail?.blocking_errors) {
        setBlockingErrors(detail.blocking_errors);
      } else {
        message.error("Failed to publish table");
      }
    },
  });

  const hasBlockers = checks.some((c) => !c.pass) || blockingErrors.length > 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal__title">
          <Rocket size={18} style={{ display: "inline", marginRight: 8, color: "var(--accent)" }} />
          {t("publish.title")}
        </h2>

        {published ? (
          <div style={{ textAlign: "center", padding: "24px 0" }}>
            <CheckCircle2 size={48} color="var(--status-production)" style={{ marginBottom: 12 }} />
            <div style={{ fontSize: 16, fontWeight: 700, color: "var(--status-production)", marginBottom: 8 }}>
              Promotion Started
            </div>
            <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 16 }}>
              The production promotion workflow has been initiated. This includes re-evaluation and regression testing of all production tables.
            </div>
            <button className="btn btn--primary btn--sm" onClick={onTrackProgress || onClose}>
              Track Progress
            </button>
          </div>
        ) : (
          <>
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 10 }}>
                {t("publish.checklist")}
              </div>
              {checks.map((c) => (
                <div key={c.label} className={`checklist-item checklist-item--${c.pass ? "pass" : "fail"}`}>
                  {c.pass
                    ? <CheckCircle2 size={15} />
                    : <XCircle size={15} />}
                  {c.label}
                </div>
              ))}
            </div>

            {blockingErrors.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--status-degraded)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 10 }}>
                  {t("publish.errors")}
                </div>
                {blockingErrors.map((e) => (
                  <div key={e.code} className="checklist-item checklist-item--fail">
                    <XCircle size={15} /> {e.message}
                  </div>
                ))}
              </div>
            )}

            <div className="modal__actions">
              <button className="btn btn--ghost" onClick={onClose}>{t("publish.cancel")}</button>
              <button
                className="btn btn--primary"
                disabled={hasBlockers || publishMutation.isPending}
                onClick={() => publishMutation.mutate()}
              >
                <Rocket size={14} />
                {publishMutation.isPending ? "Publishing..." : t("publish.confirm")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
