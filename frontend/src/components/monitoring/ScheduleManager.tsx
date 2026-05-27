import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Clock, ToggleLeft, ToggleRight, CalendarClock, ChevronDown, ChevronUp } from "lucide-react";
import dayjs from "dayjs";
import { orchestrationApi, type EvalSchedule, type EvalScheduleCreate } from "../../api/orchestration";
import { StatusBadge, Spinner, EmptySlate } from "../common/EvalUI";

// ── Cron presets ───────────────────────────────────────────────────────────────
const CRON_PRESETS = [
  { label: "Daily at 2am",   value: "0 2 * * *" },
  { label: "Daily at midnight", value: "0 0 * * *" },
  { label: "Every 6 hours",  value: "0 */6 * * *" },
  { label: "Weekly (Mon 3am)",value: "0 3 * * 1" },
  { label: "Hourly",         value: "0 * * * *" },
  { label: "Custom",         value: "__custom__" },
];

// ── Create schedule modal ──────────────────────────────────────────────────────
function CreateScheduleModal({ onClose, onSave }: { onClose: () => void; onSave: (p: EvalScheduleCreate) => void }) {
  const [form, setForm] = useState<EvalScheduleCreate>({
    dataset_id: "",
    cron_expression: "0 2 * * *",
    enabled: true,
    created_by: "user",
  });
  const [cronMode, setCronMode] = useState<string>("0 2 * * *");
  const [isCustom, setIsCustom] = useState(false);

  const handlePreset = (val: string) => {
    if (val === "__custom__") {
      setIsCustom(true);
    } else {
      setIsCustom(false);
      setCronMode(val);
      setForm(f => ({ ...f, cron_expression: val }));
    }
  };

  const valid = form.dataset_id.trim().length > 0;

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" style={{ maxWidth: 480 }}>
        <div className="modal__title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <CalendarClock size={18} style={{ color: "var(--accent)" }} />
          Create Evaluation Schedule
        </div>

        <div className="form-group">
          <label className="form-label">Dataset ID / Name</label>
          <input
            className="form-input"
            placeholder="e.g. production-dataset or all_tables"
            value={form.dataset_id}
            onChange={e => setForm(f => ({ ...f, dataset_id: e.target.value }))}
          />
        </div>

        <div className="form-group">
          <label className="form-label">Frequency</label>
          <select className="form-select" value={isCustom ? "__custom__" : cronMode} onChange={e => handlePreset(e.target.value)}>
            {CRON_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
        </div>

        {isCustom && (
          <div className="form-group">
            <label className="form-label">Cron Expression</label>
            <input
              className="form-input"
              placeholder="0 2 * * *"
              value={form.cron_expression}
              onChange={e => setForm(f => ({ ...f, cron_expression: e.target.value }))}
            />
            <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 4 }}>
              Format: minute hour day month weekday
            </div>
          </div>
        )}

        <div className="form-group">
          <label className="form-label">Table Scope (optional, comma-separated IDs)</label>
          <input
            className="form-input"
            placeholder="Leave empty for all tables"
            onChange={e => setForm(f => ({
              ...f,
              table_scope: e.target.value ? e.target.value.split(",").map(s => s.trim()) : undefined
            }))}
          />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
          <button onClick={() => setForm(f => ({ ...f, enabled: !f.enabled }))}>
            {form.enabled
              ? <ToggleRight size={24} style={{ color: "var(--status-production)" }} />
              : <ToggleLeft size={24} style={{ color: "var(--text-muted)" }} />}
          </button>
          <span style={{ fontSize: 13, color: form.enabled ? "var(--text-primary)" : "var(--text-muted)" }}>
            {form.enabled ? "Schedule enabled" : "Schedule disabled"}
          </span>
        </div>

        <div className="modal__actions">
          <button className="btn btn--ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" disabled={!valid} onClick={() => onSave(form)}>
            Create Schedule
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Schedule row ───────────────────────────────────────────────────────────────
function ScheduleRow({ schedule, onToggle, onDelete }: {
  schedule: EvalSchedule;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const presetLabel = CRON_PRESETS.find(p => p.value === schedule.cron_expression)?.label
    ?? schedule.cron_expression;

  return (
    <div style={{
      background: "var(--bg-elevated)", border: "1px solid var(--border)",
      borderRadius: 10, overflow: "hidden", marginBottom: 8,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", cursor: "pointer" }}
        onClick={() => setExpanded(e => !e)}>
        <button onClick={e => { e.stopPropagation(); onToggle(); }}
          style={{ background: "none", border: "none", cursor: "pointer", lineHeight: 0 }}>
          {schedule.enabled
            ? <ToggleRight size={22} style={{ color: "var(--status-production)" }} />
            : <ToggleLeft size={22} style={{ color: "var(--text-muted)" }} />}
        </button>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 8 }}>
            {schedule.dataset_id}
            <StatusBadge status={schedule.enabled ? "completed" : "draft"} size="sm" />
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2, display: "flex", alignItems: "center", gap: 6 }}>
            <Clock size={11} />
            {presetLabel}
            {schedule.last_run_at && <span>· Last: {dayjs(schedule.last_run_at).format("MMM D, HH:mm")}</span>}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button onClick={e => { e.stopPropagation(); onDelete(); }} className="btn btn--sm btn--danger">
            <Trash2 size={13} />
          </button>
          {expanded ? <ChevronUp size={15} style={{ color: "var(--text-muted)" }} /> : <ChevronDown size={15} style={{ color: "var(--text-muted)" }} />}
        </div>
      </div>

      {expanded && (
        <div style={{ borderTop: "1px solid var(--border-subtle)", padding: "12px 16px", background: "var(--bg-surface)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {[
              { label: "Cron Expression", value: <code style={{ fontSize: 12, color: "var(--accent-hover)", background: "var(--bg-base)", padding: "2px 6px", borderRadius: 4 }}>{schedule.cron_expression}</code> },
              { label: "Table Scope", value: schedule.table_scope?.join(", ") || "All tables" },
              { label: "Created By", value: schedule.created_by },
              { label: "Created", value: dayjs(schedule.created_at).format("MMM D, YYYY HH:mm") },
            ].map(({ label, value }) => (
              <div key={label}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
                <div style={{ fontSize: 13, color: "var(--text-primary)", fontWeight: 500 }}>{value}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── ScheduleManager ────────────────────────────────────────────────────────────
export function ScheduleManager() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);

  const { data: schedules = [], isLoading } = useQuery({
    queryKey: ["eval-schedules"],
    queryFn: orchestrationApi.listSchedules,
    refetchInterval: 30_000,
  });

  const createMut = useMutation({
    mutationFn: orchestrationApi.createSchedule,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["eval-schedules"] }); setShowCreate(false); },
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      orchestrationApi.updateSchedule(id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval-schedules"] }),
  });

  const deleteMut = useMutation({
    mutationFn: orchestrationApi.deleteSchedule,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval-schedules"] }),
  });

  if (isLoading) return (
    <div style={{ display: "flex", justifyContent: "center", padding: 32 }}>
      <Spinner size={24} />
    </div>
  );

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 8 }}>
            <CalendarClock size={15} style={{ color: "var(--accent)" }} />
            Scheduled Runs
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
            {schedules.length} schedule{schedules.length !== 1 ? "s" : ""} configured
          </div>
        </div>
        <button className="btn btn--primary btn--sm" onClick={() => setShowCreate(true)}>
          <Plus size={14} /> New Schedule
        </button>
      </div>

      {schedules.length === 0 ? (
        <EmptySlate
          icon={<CalendarClock size={40} />}
          title="No schedules configured"
          sub="Create a schedule to automatically run evaluations on a cron cadence"
        />
      ) : (
        schedules.map(s => (
          <ScheduleRow
            key={s.id}
            schedule={s}
            onToggle={() => toggleMut.mutate({ id: s.id, enabled: !s.enabled })}
            onDelete={() => { if (confirm("Delete this schedule?")) deleteMut.mutate(s.id); }}
          />
        ))
      )}

      {showCreate && (
        <CreateScheduleModal
          onClose={() => setShowCreate(false)}
          onSave={p => createMut.mutate(p)}
        />
      )}
    </div>
  );
}
