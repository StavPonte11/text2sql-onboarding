import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { BarChart2, RefreshCw, Lightbulb, Table2, Link2, AlertTriangle } from "lucide-react";
import { profilingApi } from "../../api/client";
import type { ColumnProfile, CrossTableProfile } from "../../types";

// ── helpers ───────────────────────────────────────────────────────────────────
function pct(v?: number) {
  if (v === undefined || v === null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}
function fmt(v?: number | null) {
  if (v === undefined || v === null) return "—";
  return v.toLocaleString();
}

// ── ColumnProfileCard ─────────────────────────────────────────────────────────
export function ColumnProfileCard({ col }: { col: ColumnProfile }) {
  const nullColor =
    (col.null_rate ?? 0) > 0.2
      ? "var(--status-degraded)"
      : (col.null_rate ?? 0) > 0.05
      ? "var(--status-sandbox)"
      : "var(--status-production)";

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: "14px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text)" }}>
          {col.column_name}
        </span>
        <div style={{ display: "flex", gap: 4 }}>
          {col.is_time && <span className="badge badge--neutral" style={{ fontSize: 10, padding: "2px 6px" }}>Time</span>}
          {col.is_geo && <span className="badge badge--neutral" style={{ fontSize: 10, padding: "2px 6px" }}>Geo</span>}
          {col.data_type && (
            <span style={{ fontSize: 10, color: "var(--text-muted)", background: "var(--bg-base)", borderRadius: 4, padding: "2px 6px", border: "1px solid var(--border-subtle)" }}>
              {col.data_type}
            </span>
          )}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
        {[
          { label: "Distinct", value: fmt(col.distinct_count) },
          { label: "Nulls", value: pct(col.null_rate), color: nullColor },
          { label: "Avg", value: col.avg_value != null ? col.avg_value.toFixed(2) : "—" },
          { label: "Min", value: col.min_value ?? "—" },
          { label: "Max", value: col.max_value ?? "—" },
          { label: "Null #", value: fmt(col.null_count) },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ background: "var(--bg-base)", borderRadius: 6, padding: "6px 8px" }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 12, fontWeight: 700, color: color ?? "var(--text)" }}>{value}</div>
          </div>
        ))}
      </div>

      {col.top_values && col.top_values.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Top Values</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {col.top_values.slice(0, 5).map((tv) => (
              <span key={tv.value} style={{
                fontSize: 10,
                background: "var(--accent-dim)",
                color: "var(--accent-hover)",
                borderRadius: 4,
                padding: "2px 6px",
              }}>
                {tv.value} ({tv.count.toLocaleString()})
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── CrossTableCard ────────────────────────────────────────────────────────────
function CrossTableCard({ profile }: { profile: CrossTableProfile }) {
  const isStrong = profile.match_strength === "strong";
  return (
    <div style={{
      border: `1px solid ${isStrong ? "var(--status-production)" : "var(--border)"}`,
      borderRadius: 8, padding: "12px 14px",
      background: isStrong ? "rgba(34,197,94,0.04)" : "var(--bg-card)",
      display: "flex", flexDirection: "column", gap: 6,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Link2 size={14} color={isStrong ? "var(--status-production)" : "var(--text-muted)"} />
          <code style={{ fontSize: 11, color: "var(--text)" }}>{profile.target_table_id.slice(0, 8)}…</code>
        </div>
        <span style={{
          fontSize: 10, fontWeight: 700, borderRadius: 4, padding: "2px 8px",
          background: isStrong ? "var(--status-production)" : "var(--border)",
          color: isStrong ? "#fff" : "var(--text-muted)",
        }}>
          {isStrong ? "Strong Match" : "Weak Match"}
        </span>
      </div>
      {profile.join_suggestion && (
        <code style={{ fontSize: 11, color: "var(--text-muted)", background: "var(--bg-base)", borderRadius: 4, padding: "4px 8px" }}>
          {profile.join_suggestion}
        </code>
      )}
      {profile.common_columns && profile.common_columns.length > 0 && (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {profile.common_columns.map((c) => (
            <span key={c} className="badge badge--neutral" style={{ fontSize: 10, padding: "2px 6px" }}>{c}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── ProfilingTab ──────────────────────────────────────────────────────────────
export function ProfilingTab({ tableId }: { tableId: string }) {
  const qc = useQueryClient();
  const [activeSection, setActiveSection] = useState<"overview" | "columns" | "sample" | "cross" | "insights">("overview");

  const profileQ = useQuery({
    queryKey: ["profile", tableId],
    queryFn: () => profilingApi.get(tableId),
    retry: false,
  });

  const columnsQ = useQuery({
    queryKey: ["profile-columns", tableId],
    queryFn: () => profilingApi.getColumns(tableId),
  });

  const crossQ = useQuery({
    queryKey: ["cross-profiles", tableId],
    queryFn: () => profilingApi.getCrossProfiles(tableId),
  });

  const runMutation = useMutation({
    mutationFn: () => profilingApi.run(tableId),
    onSuccess: () => {
      // Poll until completed
      const poll = setInterval(async () => {
        await qc.invalidateQueries({ queryKey: ["profile", tableId] });
        await qc.invalidateQueries({ queryKey: ["profile-columns", tableId] });
        const fresh = qc.getQueryData<{ status: string }>(["profile", tableId]);
        if (fresh?.status === "completed" || fresh?.status === "failed") clearInterval(poll);
      }, 2000);
    },
  });

  const profile = profileQ.data;
  const isRunning = profile?.status === "running" || runMutation.isPending;

  const sections = [
    { key: "overview", label: "Overview" },
    { key: "columns", label: `Columns (${columnsQ.data?.length ?? 0})` },
    { key: "sample", label: "Sample Data" },
    { key: "cross", label: `Cross-Table (${crossQ.data?.length ?? 0})` },
    { key: "insights", label: "Auto Insights" },
  ] as const;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Header */}
      <div className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 20px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <BarChart2 size={18} color="var(--accent-hover)" />
          <div>
            <div style={{ fontWeight: 700, fontSize: 14 }}>Data Profiling</div>
            {profile?.cached_until && (
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                Cached until {new Date(profile.cached_until).toLocaleString()}
              </div>
            )}
          </div>
        </div>
        <button
          className={`btn btn--primary btn--sm${isRunning ? " btn--loading" : ""}`}
          onClick={() => runMutation.mutate()}
          disabled={isRunning}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <RefreshCw size={13} className={isRunning ? "spin" : ""} />
          {isRunning ? "Running…" : profile ? "Re-profile" : "Run Profiling"}
        </button>
      </div>

      {!profile && !isRunning && (
        <div className="card">
          <div className="empty-state">
            <BarChart2 size={36} className="empty-state__icon" />
            <div className="empty-state__text">No profile yet</div>
            <div className="empty-state__sub">Click "Run Profiling" to analyze this table with Trino sampling queries.</div>
          </div>
        </div>
      )}

      {isRunning && (
        <div className="card" style={{ textAlign: "center", padding: 40 }}>
          <div className="spinner" style={{ margin: "0 auto 12px" }} />
          <div style={{ fontSize: 13, color: "var(--text-muted)" }}>Profiling in progress — running APPROX queries via Trino…</div>
        </div>
      )}

      {profile && profile.status === "completed" && (
        <>
          {/* Section tabs */}
          <div className="tabs">
            {sections.map((s) => (
              <div
                key={s.key}
                className={`tab-item${activeSection === s.key ? " tab-item--active" : ""}`}
                onClick={() => setActiveSection(s.key)}
              >
                {s.label}
              </div>
            ))}
          </div>

          {/* Overview */}
          {activeSection === "overview" && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
              {[
                { label: "Row Count", value: fmt(profile.row_count), icon: "📊" },
                { label: "Columns", value: fmt(profile.column_count), icon: "🗂" },
                { label: "Avg Null Rate", value: pct(profile.null_rate_avg), icon: "🕳" },
                { label: "Duplicate Rate", value: pct(profile.duplicate_rate), icon: "♻️" },
                { label: "Size", value: profile.size_bytes ? `${(profile.size_bytes / 1e6).toFixed(1)} MB` : "—", icon: "💾" },
                { label: "Status", value: profile.status, icon: "✅" },
              ].map(({ label, value, icon }) => (
                <div key={label} className="card" style={{ textAlign: "center", padding: "16px 12px" }}>
                  <div style={{ fontSize: 22, marginBottom: 4 }}>{icon}</div>
                  <div style={{ fontSize: 20, fontWeight: 800, color: "var(--accent-hover)" }}>{value}</div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{label}</div>
                </div>
              ))}
            </div>
          )}

          {/* Columns */}
          {activeSection === "columns" && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 12 }}>
              {columnsQ.isLoading && <div className="card"><div className="skeleton" style={{ height: 80 }} /></div>}
              {columnsQ.data?.map((col) => <ColumnProfileCard key={col.id} col={col} />)}
              {!columnsQ.isLoading && !columnsQ.data?.length && (
                <div className="card"><div className="empty-state"><div className="empty-state__text">No column profiles yet</div></div></div>
              )}
            </div>
          )}

          {/* Sample Data */}
          {activeSection === "sample" && (
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
              {profile.sample_data && profile.sample_data.length > 0 ? (
                <div style={{ overflowX: "auto" }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        {Object.keys(profile.sample_data[0]).map((k) => <th key={k}>{k}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {profile.sample_data.map((row, i) => (
                        <tr key={i}>
                          {Object.values(row).map((v, j) => (
                            <td key={j} style={{ fontSize: 12 }}>{String(v)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="empty-state"><div className="empty-state__text">No sample data</div></div>
              )}
            </div>
          )}

          {/* Cross-Table */}
          {activeSection === "cross" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {crossQ.data && crossQ.data.length > 0 ? (
                crossQ.data.map((cp) => <CrossTableCard key={cp.id} profile={cp} />)
              ) : (
                <div className="card">
                  <div className="empty-state">
                    <Link2 size={32} className="empty-state__icon" />
                    <div className="empty-state__text">No join candidates found</div>
                    <div className="empty-state__sub">Cross-table analysis runs automatically when multiple tables share column names.</div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Insights */}
          {activeSection === "insights" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {profile.auto_insights && profile.auto_insights.length > 0 ? (
                profile.auto_insights.map((insight, i) => (
                  <div key={i} className="card" style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "12px 16px" }}>
                    <Lightbulb size={16} color="var(--status-sandbox)" style={{ flexShrink: 0, marginTop: 2 }} />
                    <span style={{ fontSize: 13, color: "var(--text)" }}>{insight}</span>
                  </div>
                ))
              ) : (
                <div className="card">
                  <div className="empty-state">
                    <Lightbulb size={32} className="empty-state__icon" />
                    <div className="empty-state__text">No insights generated</div>
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {profile?.status === "failed" && (
        <div className="card" style={{ borderColor: "var(--status-degraded)" }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", color: "var(--status-degraded)" }}>
            <AlertTriangle size={16} />
            <span style={{ fontSize: 13 }}>Profiling job failed. Check Trino connection and try again.</span>
          </div>
        </div>
      )}
    </div>
  );
}
