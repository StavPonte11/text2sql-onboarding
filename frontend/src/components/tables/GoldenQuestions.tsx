import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus, Trash2, HelpCircle, Upload } from "lucide-react";
import { App } from "antd";
import { questionsApi } from "../../api/client";
import type { GoldenQuestionCreate, DifficultyLevel } from "../../types";
import { SkeletonTable } from "../common/Skeleton";
import { ErrorState } from "../common/ErrorState";
import dayjs from "dayjs";
import "./GoldenQuestions.css";

interface Props { tableId: string }

const DIFFICULTY_COLORS: Record<DifficultyLevel, string> = {
  simple:  "var(--status-production)",
  medium:  "var(--status-sandbox)",
  complex: "var(--status-degraded)",
};

const EMPTY_FORM: GoldenQuestionCreate = { question: "", expected_sql: "", difficulty: "simple", question_type: "simple" };

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

  const uploadMutation = useMutation({
    mutationFn: (file: File) => questionsApi.uploadQuestions(tableId, file),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["questions", tableId] });
      message.success(res.message || "Questions uploaded successfully");
    },
    onError: (err: any) => {
      message.error(err.response?.data?.detail || "Failed to upload questions");
    }
  });

  const deleteMutation = useMutation({
    mutationFn: (qid: string) => questionsApi.delete(tableId, qid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["questions", tableId] });
      message.success("Golden question deleted");
    },
  });

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      uploadMutation.mutate(file);
      // reset input
      e.target.value = "";
    }
  };

  if (isLoading) return <SkeletonTable rows={4} cols={4} />;
  if (isError) return <ErrorState onRetry={refetch} />;

  return (
    <div>
      <div className="questions-header">
        <h2 className="questions-title">{t("questions.title")}</h2>
        <div className="questions-actions">
          <input 
            type="file" 
            id="bulk-upload-input" 
            className="file-upload-input" 
            accept=".json,.xlsx,.xls"
            onChange={handleFileUpload}
          />
          <button 
            className="btn btn--ghost btn--sm" 
            onClick={() => document.getElementById("bulk-upload-input")?.click()}
            disabled={uploadMutation.isPending}
          >
            <Upload size={14} /> {uploadMutation.isPending ? "Uploading..." : "Upload JSON/Excel"}
          </button>
          <button className="btn btn--primary btn--sm" onClick={() => setShowAdd(true)}>
            <Plus size={14} /> {t("questions.add")}
          </button>
        </div>
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
        <div className="card questions-table-card">
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
                  <td className="question-text-cell">
                    <span className="question-text">{q.question}</span>
                  </td>
                  <td>
                    <code className="sql-code-snippet">
                      {q.expected_sql.length > 60 ? q.expected_sql.slice(0, 60) + "…" : q.expected_sql}
                    </code>
                  </td>
                  <td>
                    <span className="difficulty-badge" style={{ color: DIFFICULTY_COLORS[q.difficulty] }}>
                      {q.difficulty}
                    </span>
                  </td>
                  <td className="created-date-cell">
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
