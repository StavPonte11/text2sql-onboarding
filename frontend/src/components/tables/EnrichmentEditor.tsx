import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Save, AlertCircle, MapPin, Clock, Check } from "lucide-react";
import { App } from "antd";
import { enrichmentApi } from "../../api/client";
import type { EnrichmentData, ColumnDef } from "../../types";
import { SkeletonCard } from "../common/Skeleton";

interface Props { tableId: string }

function validateEnrichment(data: EnrichmentData): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!data.table_description || data.table_description.length < 20) {
    errors["table_description"] = "Table description must be at least 20 characters";
  }
  data.columns.forEach((col, i) => {
    if (!col.description) {
      errors[`col_${i}`] = "Description is required";
    } else if (col.description.length < 20) {
      errors[`col_${i}`] = "Description must be at least 20 characters";
    }
  });
  return errors;
}

export function EnrichmentEditor({ tableId }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { message } = App.useApp();

  const { data, isLoading } = useQuery({
    queryKey: ["enrichment", tableId],
    queryFn: () => enrichmentApi.getLatest(tableId),
    retry: false,
  });

  const [form, setForm] = useState<EnrichmentData>({ table_description: "", columns: [] });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data?.data) setForm(data.data);
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: () => enrichmentApi.create(tableId, form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["enrichment", tableId] });
      setSaved(true);
      message.success("Enrichment saved successfully");
      setTimeout(() => setSaved(false), 2500);
    },
  });

  const handleSave = () => {
    const errs = validateEnrichment(form);
    setErrors(errs);
    if (Object.keys(errs).length === 0) saveMutation.mutate();
  };

  const updateColumn = (i: number, patch: Partial<ColumnDef>) =>
    setForm((f) => ({ ...f, columns: f.columns.map((c, idx) => idx === i ? { ...c, ...patch } : c) }));

  const addColumn = () =>
    setForm((f) => ({ ...f, columns: [...f.columns, { name: "", description: "" }] }));

  const removeColumn = (i: number) =>
    setForm((f) => ({ ...f, columns: f.columns.filter((_, idx) => idx !== i) }));

  if (isLoading) return <SkeletonCard />;

  return (
    <div>
      <div className="flex items-center" style={{ justifyContent: "space-between", marginBottom: 20 }}>
        <h2 style={{ fontSize: 17, fontWeight: 700 }}>{t("enrichment.title")}</h2>
        <div className="flex gap-2 items-center">
          {saved && <span style={{ color: "var(--status-production)", fontSize: 13, fontWeight: 600, display: "flex", alignItems: "center", gap: 4 }}><Check size={14} /> Saved</span>}
          <button className="btn btn--primary btn--sm" onClick={handleSave} disabled={saveMutation.isPending}>
            <Save size={14} /> {saveMutation.isPending ? "Saving..." : t("enrichment.save")}
          </button>
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">{t("enrichment.tableDesc")}</label>
        <textarea
          className="form-textarea" rows={4}
          value={form.table_description}
          onChange={(e) => setForm((f) => ({ ...f, table_description: e.target.value }))}
          placeholder="Describe what this table contains, its purpose, and usage context..."
        />
        {errors["table_description"] && (
          <div className="form-error"><AlertCircle size={12} />{errors["table_description"]}</div>
        )}
      </div>

      <div style={{ marginBottom: 12, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, color: "var(--text-secondary)" }}>{t("enrichment.columns")}</h3>
        <button className="btn btn--ghost btn--sm" onClick={addColumn}>+ Add Column</button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {form.columns.length === 0 && (
          <div className="card">
            <div className="empty-state" style={{ padding: "28px 0" }}>
              <div className="empty-state__text">No columns defined</div>
              <div className="empty-state__sub">Click "Add Column" to start enriching</div>
            </div>
          </div>
        )}
        {form.columns.map((col, i) => (
          <div key={i} className="card card--elevated" style={{ padding: 14 }}>
            <div style={{ display: "grid", gridTemplateColumns: "180px 1fr auto", gap: 10, alignItems: "start" }}>
              <div>
                <label className="form-label">{t("enrichment.colName")}</label>
                <input className="form-input" value={col.name}
                  onChange={(e) => updateColumn(i, { name: e.target.value })} placeholder="column_name" />
              </div>
              <div>
                <label className="form-label">{t("enrichment.colDesc")}</label>
                <input className="form-input" value={col.description}
                  onChange={(e) => updateColumn(i, { description: e.target.value })}
                  placeholder="Describe this column..." />
                {errors[`col_${i}`] && (
                  <div className="form-error"><AlertCircle size={12} />{errors[`col_${i}`]}</div>
                )}
              </div>
              <div style={{ paddingTop: 22 }}>
                <button className="btn btn--danger btn--sm" onClick={() => removeColumn(i)}>×</button>
              </div>
            </div>
            <div className="flex gap-2 mt-4">
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, color: "var(--text-secondary)", cursor: "pointer" }}>
                <input type="checkbox" checked={col.is_geo ?? false}
                  onChange={(e) => updateColumn(i, { is_geo: e.target.checked })}
                  style={{ accentColor: "var(--accent)" }} />
                <MapPin size={12} /> {t("enrichment.isGeo")}
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, color: "var(--text-secondary)", cursor: "pointer" }}>
                <input type="checkbox" checked={col.is_time ?? false}
                  onChange={(e) => updateColumn(i, { is_time: e.target.checked })}
                  style={{ accentColor: "var(--accent)" }} />
                <Clock size={12} /> {t("enrichment.isTime")}
              </label>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
