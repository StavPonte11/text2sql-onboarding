import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CloudDownload } from "lucide-react";
import { App } from "antd";
import { tablesApi, enrichmentApi, questionsApi } from "../../api/client";
import type { TableCreate, EnrichmentData, GoldenQuestionCreate } from "../../types";

const STEPS = ["select", "schema", "enrichment", "validate", "questions", "submit"] as const;
type Step = (typeof STEPS)[number];

export function OnboardingWizard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { message } = App.useApp();

  const [currentStep, setCurrentStep] = useState(0);
  const [done, setDone] = useState(false);

  const [tableForm, setTableForm] = useState<TableCreate>({ name: "", schema_name: "public", owner_id: "user-1" });
  const [enrichmentForm, setEnrichmentForm] = useState<EnrichmentData>({ table_description: "", columns: [] });
  const [questions, setQuestions] = useState<GoldenQuestionCreate[]>([]);
  const [createdTableId, setCreatedTableId] = useState<string | null>(null);
  const [isFetchingSchema, setIsFetchingSchema] = useState(false);

  const createTableMutation = useMutation({ mutationFn: tablesApi.create });
  const createEnrichmentMutation = useMutation({ mutationFn: ({ id, data }: { id: string; data: EnrichmentData }) => enrichmentApi.create(id, data) });
  const createQuestionMutation = useMutation({ mutationFn: ({ id, q }: { id: string; q: GoldenQuestionCreate }) => questionsApi.create(id, q) });

  const step = STEPS[currentStep];

  const canNext = () => {
    if (step === "select") return !!tableForm.name && !!tableForm.schema_name;
    if (step === "enrichment") return enrichmentForm.table_description.length >= 20;
    if (step === "validate") {
      return enrichmentForm.table_description.length >= 20 &&
        enrichmentForm.columns.every((c) => c.description.length >= 20);
    }
    return true;
  };

  const handleFetchSchema = () => {
    setIsFetchingSchema(true);
    setTimeout(() => {
      setEnrichmentForm((f) => ({
        ...f,
        columns: [
          { name: "id", description: "" },
          { name: "created_at", description: "" },
          { name: "status", description: "" },
        ]
      }));
      setIsFetchingSchema(false);
      message.success(`Fetched 3 columns for ${tableForm.name} from OpenMetadata`);
    }, 1500);
  };

  const handleNext = async () => {
    if (step === "select") {
      const t = await createTableMutation.mutateAsync(tableForm);
      setCreatedTableId(t.id);
      // Ensure columns aren't overwritten if already fetched
      setEnrichmentForm((f) => ({ ...f, columns: f.columns.length ? f.columns : [] }));
    }
    if (step === "enrichment" && createdTableId) {
      await createEnrichmentMutation.mutateAsync({ id: createdTableId, data: enrichmentForm });
      message.success("Enrichment saved successfully");
    }
    if (step === "questions" && createdTableId) {
      for (const q of questions) {
        await createQuestionMutation.mutateAsync({ id: createdTableId, q });
      }
      message.success("Golden questions added");
      qc.invalidateQueries({ queryKey: ["tables"] });
      setDone(true);
      return;
    }
    setCurrentStep((s) => Math.min(s + 1, STEPS.length - 1));
  };

  const addQuestion = () => setQuestions((q) => [...q, { question: "", expected_sql: "", difficulty: "simple" }]);
  const updateQuestion = (i: number, patch: Partial<GoldenQuestionCreate>) =>
    setQuestions((qs) => qs.map((q, idx) => idx === i ? { ...q, ...patch } : q));

  if (done) {
    return (
      <div className="page">
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "80px 0", gap: 16 }}>
          <CheckCircle2 size={64} color="var(--status-production)" />
          <h2 style={{ fontSize: 22, fontWeight: 700 }}>{t("wizard.finish")}</h2>
          <p style={{ color: "var(--text-muted)" }}>Table ID: {createdTableId}</p>
          <div className="flex gap-2">
            <button className="btn btn--ghost" onClick={() => navigate("/tables")}>View All Tables</button>
            <button className="btn btn--primary" onClick={() => navigate(`/tables/${createdTableId}`)}>Go to Table</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">{t("wizard.title")}</h1>
          <p className="page__subtitle">Step {currentStep + 1} of {STEPS.length}</p>
        </div>
      </div>

      {/* Stepper */}
      <div className="stepper" style={{ marginBottom: 32 }}>
        {STEPS.map((s, i) => (
          <div key={s} className="stepper__step">
            <div className={`stepper__circle${i < currentStep ? " stepper__circle--done" : i === currentStep ? " stepper__circle--active" : ""}`}>
              {i < currentStep ? "✓" : i + 1}
            </div>
            <div className={`stepper__label${i === currentStep ? " stepper__label--active" : ""}`}>
              {t(`wizard.steps.${s}`)}
            </div>
            {i < STEPS.length - 1 && (
              <div className={`stepper__line${i < currentStep ? " stepper__line--done" : ""}`} />
            )}
          </div>
        ))}
      </div>

      {/* Step content */}
      <div className="card" style={{ marginBottom: 24 }}>
        {step === "select" && (
          <div>
            <h3 style={{ fontWeight: 700, marginBottom: 16 }}>{t("wizard.steps.select")}</h3>
            <div className="form-group">
              <label className="form-label">Table Name</label>
              <input className="form-input" value={tableForm.name}
                onChange={(e) => setTableForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. orders" />
            </div>
            <div className="form-group">
              <label className="form-label">Schema</label>
              <input className="form-input" value={tableForm.schema_name}
                onChange={(e) => setTableForm((f) => ({ ...f, schema_name: e.target.value }))}
                placeholder="e.g. public" />
            </div>
            <div className="form-group">
              <label className="form-label">Owner ID</label>
              <input className="form-input" value={tableForm.owner_id}
                onChange={(e) => setTableForm((f) => ({ ...f, owner_id: e.target.value }))} />
            </div>
          </div>
        )}

        {step === "schema" && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                <h3 style={{ fontWeight: 700 }}>{t("wizard.steps.schema")}</h3>
                <button className="btn btn--ghost btn--sm" onClick={handleFetchSchema} disabled={isFetchingSchema}>
                    <CloudDownload size={14} />
                    {isFetchingSchema ? "Fetching..." : "Fetch Schema"}
                </button>
            </div>
            <div style={{ background: "var(--bg-base)", borderRadius: 8, padding: 16 }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>Table: <code>{tableForm.name}</code></div>
              <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Schema: <code>{tableForm.schema_name}</code></div>
              
              {enrichmentForm.columns.length > 0 ? (
                  <div style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                      <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>Discovered Columns:</div>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                          {enrichmentForm.columns.map((col, i) => (
                              <code key={i} style={{ padding: "4px 8px", background: "var(--bg-hover)", borderRadius: 4, fontSize: 12 }}>{col.name}</code>
                          ))}
                      </div>
                  </div>
              ) : (
                  <div style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 4 }}>
                    The actual schema will be fetched from your OpenMetadata catalog in production. Click "Fetch Schema" to simulate.
                  </div>
              )}
            </div>
          </div>
        )}

        {(step === "enrichment" || step === "validate") && (
          <div>
            <h3 style={{ fontWeight: 700, marginBottom: 16 }}>
              {step === "validate" ? t("wizard.steps.validate") : t("wizard.steps.enrichment")}
            </h3>
            <div className="form-group">
              <label className="form-label">Table Description</label>
              <textarea className="form-textarea" rows={3}
                value={enrichmentForm.table_description}
                onChange={(e) => setEnrichmentForm((f) => ({ ...f, table_description: e.target.value }))}
                placeholder="Describe this table in at least 20 characters..." />
              <div className="text-sm text-muted" style={{ marginTop: 4 }}>
                {enrichmentForm.table_description.length} / 20 chars minimum
              </div>
            </div>
            <div style={{ marginBottom: 10, display: "flex", justifyContent: "space-between" }}>
              <label className="form-label" style={{ marginBottom: 0 }}>Columns</label>
              <button className="btn btn--ghost btn--sm"
                onClick={() => setEnrichmentForm((f) => ({ ...f, columns: [...f.columns, { name: "", description: "" }] }))}>
                + Add Column
              </button>
            </div>
            {enrichmentForm.columns.map((col, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, marginBottom: 8 }}>
                <input className="form-input" placeholder="column_name" value={col.name}
                  onChange={(e) => setEnrichmentForm((f) => ({ ...f, columns: f.columns.map((c, idx) => idx === i ? { ...c, name: e.target.value } : c) }))} />
                <input className="form-input" placeholder="Description (min 20 chars)" value={col.description}
                  onChange={(e) => setEnrichmentForm((f) => ({ ...f, columns: f.columns.map((c, idx) => idx === i ? { ...c, description: e.target.value } : c) }))} />
                <button className="btn btn--danger btn--sm"
                  onClick={() => setEnrichmentForm((f) => ({ ...f, columns: f.columns.filter((_, idx) => idx !== i) }))}>×</button>
              </div>
            ))}
          </div>
        )}

        {step === "questions" && (
          <div>
            <h3 style={{ fontWeight: 700, marginBottom: 16 }}>{t("wizard.steps.questions")}</h3>
            {questions.map((q, i) => (
              <div key={i} className="card card--elevated" style={{ padding: 12, marginBottom: 10 }}>
                <div className="form-group">
                  <label className="form-label">Question</label>
                  <input className="form-input" value={q.question}
                    onChange={(e) => updateQuestion(i, { question: e.target.value })}
                    placeholder="How many orders were placed last month?" />
                </div>
                <div className="form-group">
                  <label className="form-label">Expected SQL</label>
                  <textarea className="form-textarea" rows={2} value={q.expected_sql}
                    onChange={(e) => updateQuestion(i, { expected_sql: e.target.value })}
                    placeholder="SELECT COUNT(*) FROM ..." />
                </div>
                <select className="form-select" value={q.difficulty}
                  onChange={(e) => updateQuestion(i, { difficulty: e.target.value as any })}>
                  <option value="simple">Simple</option>
                  <option value="medium">Medium</option>
                  <option value="complex">Complex</option>
                </select>
              </div>
            ))}
            <button className="btn btn--ghost btn--sm" onClick={addQuestion}>+ Add Question</button>
          </div>
        )}

        {step === "submit" && (
          <div style={{ textAlign: "center", padding: "24px 0" }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Ready to submit!</div>
            <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
              Table <strong>{tableForm.name}</strong> with {enrichmentForm.columns.length} columns and {questions.length} golden questions.
            </div>
          </div>
        )}
      </div>

      {/* Navigation */}
      <div className="flex gap-2" style={{ justifyContent: "flex-end" }}>
        {currentStep > 0 && (
          <button className="btn btn--ghost" onClick={() => setCurrentStep((s) => s - 1)}>
            {t("wizard.back")}
          </button>
        )}
        <button
          className="btn btn--primary"
          disabled={!canNext() || createTableMutation.isPending || createEnrichmentMutation.isPending}
          onClick={handleNext}
        >
          {step === "questions" || step === "submit" ? t("wizard.submit") : t("wizard.next")}
        </button>
      </div>
    </div>
  );
}
