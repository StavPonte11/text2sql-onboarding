import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus, Trash2, HelpCircle } from "lucide-react";
import { App } from "antd";
import { questionsApi } from "../../api/client";
import type { GoldenQuestionCreate, DifficultyLevel } from "../../types";
import { SkeletonTable } from "../common/Skeleton";
import { ErrorState } from "../common/ErrorState";
import dayjs from "dayjs";

interface Props { tableId: string }

const DIFFICULTY_COLORS: Record<DifficultyLevel, string> = {
  simple:  "var(--status-production)",
  medium:  "var(--status-sandbox)",
  complex: "var(--status-degraded)",
};

const EMPTY_FORM: GoldenQuestionCreate = { question: "", expected_sql: "", difficulty: "simple" };

export function GoldenQuestions({ tableId }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState<GoldenQuestionCreate>(EMPTY_FORM);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["questions", tableId],
    queryFn: () => questionsApi.list(tableId),
  });

  const addMutation = useMutation({
    mutationFn: () => questionsApi.create(tableId, form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["questions", tableId] });
      setForm(EMPTY_FORM);
      setShowAdd(false);
      message.success("Golden question added");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (qid: string) => questionsApi.delete(tableId, qid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["questions", tableId] });
      message.success("Golden question deleted");
    },
  });

  if (isLoading) return <SkeletonTable rows={4} cols={4} />;
  if (isError) return <ErrorState onRetry={refetch} />;

  return (
    <div>
      <div className="flex items-center" style={{ justifyContent: "space-between", marginBottom: 20 }}>
        <h2 style={{ fontSize: 17, fontWeight: 700 }}>{t("questions.title")}</h2>
        <button className="btn btn--primary btn--sm" onClick={() => setShowAdd(true)}>
          <Plus size={14} /> {t("questions.add")}
        </button>
      </div>

      {(!data || data.length === 0) ? (
        <div className="card">
          <div className="empty-state">
            <HelpCircle size={36} className="empty-state__icon" />
            <div className="empty-state__text">{t("questions.noData")}</div>
            <div className="empty-state__sub">Golden questions are used to evaluate TextToSQL accuracy</div>
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("questions.question")}</th>
                <th>{t("questions.sql")}</th>
                <th>{t("questions.difficulty")}</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.map((q) => (
                <tr key={q.id}>
                  <td style={{ maxWidth: 260 }}>
                    <span style={{ fontWeight: 500 }}>{q.question}</span>
                  </td>
                  <td>
                    <code style={{ fontSize: 12, color: "var(--text-muted)", background: "var(--bg-base)", padding: "2px 6px", borderRadius: 4 }}>
                      {q.expected_sql.length > 60 ? q.expected_sql.slice(0, 60) + "…" : q.expected_sql}
                    </code>
                  </td>
                  <td>
                    <span style={{ color: DIFFICULTY_COLORS[q.difficulty], fontWeight: 600, fontSize: 12 }}>
                      {q.difficulty}
                    </span>
                  </td>
                  <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    {dayjs(q.created_at).format("MMM D, YYYY")}
                  </td>
                  <td>
                    <button
                      className="btn btn--danger btn--sm"
                      onClick={() => deleteMutation.mutate(q.id)}
                      disabled={deleteMutation.isPending}
                    >
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showAdd && (
        <div className="modal-overlay" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal__title">{t("questions.add")}</h2>
            <div className="form-group">
              <label className="form-label">{t("questions.question")}</label>
              <input className="form-input" value={form.question}
                onChange={(e) => setForm((f) => ({ ...f, question: e.target.value }))}
                placeholder="How many orders were placed last month?" />
            </div>
            <div className="form-group">
              <label className="form-label">{t("questions.sql")}</label>
              <textarea className="form-textarea" rows={3} value={form.expected_sql}
                onChange={(e) => setForm((f) => ({ ...f, expected_sql: e.target.value }))}
                placeholder="SELECT COUNT(*) FROM orders WHERE ..." />
            </div>
            <div className="form-group">
              <label className="form-label">{t("questions.difficulty")}</label>
              <select className="form-select" value={form.difficulty}
                onChange={(e) => setForm((f) => ({ ...f, difficulty: e.target.value as DifficultyLevel }))}>
                <option value="simple">Simple</option>
                <option value="medium">Medium</option>
                <option value="complex">Complex</option>
              </select>
            </div>
            <div className="modal__actions">
              <button className="btn btn--ghost" onClick={() => setShowAdd(false)}>{t("common.cancel")}</button>
              <button className="btn btn--primary" onClick={() => addMutation.mutate()}
                disabled={!form.question || !form.expected_sql || addMutation.isPending}>
                {addMutation.isPending ? "Adding..." : t("common.save")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
