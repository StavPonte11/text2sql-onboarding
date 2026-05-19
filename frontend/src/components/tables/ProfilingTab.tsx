import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { BarChart2, RefreshCw, Lightbulb, Link2, AlertTriangle, ChevronRight, Layers, Maximize2, LayoutGrid, AlignLeft } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, AreaChart, Area } from "recharts";
import { profilingApi } from "../../api/client";
import type { ColumnProfile, CrossTableProfile, RowField } from "../../types";

// ── helpers ───────────────────────────────────────────────────────────────────
function pct(v?: number) {
  if (v === undefined || v === null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}
function fmt(v?: number | null) {
  if (v === undefined || v === null) return "—";
  return v.toLocaleString();
}
function fmtDate(ts: any) {
  if (!ts) return "—";
  if (typeof ts === 'number') {
    return new Date(ts * 1000).toISOString().split('T')[0];
  }
  return String(ts).split(' ')[0];
}

const SEMANTIC_COLORS: Record<string, string> = {
  row: "#818cf8",
  categorical: "#34d399",
  continuous: "#60a5fa",
  time: "#f59e0b",
  geo: "#fb923c",
  complex: "#94a3b8",
};

// ── NestedFieldExplorer ───────────────────────────────────────────────────────
function NestedFieldExplorer({ rootChildren, rootDataType }: { rootChildren: RowField[], rootDataType?: string }) {
  const [path, setPath] = useState<RowField[]>([]);

  const currentLevelFields = path.length > 0 ? (path[path.length - 1].stats?.children ?? []) : rootChildren;

  return (
    <div style={{ background: "var(--bg-base)", padding: 24, borderRadius: 12 }}>
      {/* Breadcrumbs */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20, fontSize: 13, fontWeight: 500 }}>
        <span 
          style={{ cursor: "pointer", color: path.length === 0 ? "var(--text)" : "var(--accent-hover)" }}
          onClick={() => setPath([])}
        >
          Root Object
        </span>
        {path.map((p, idx) => (
          <div key={idx} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <ChevronRight size={14} color="var(--text-muted)" />
            <span 
              style={{ cursor: "pointer", color: idx === path.length - 1 ? "var(--text)" : "var(--accent-hover)" }}
              onClick={() => setPath(path.slice(0, idx + 1))}
            >
              {p.name}
            </span>
          </div>
        ))}
      </div>

      {rootDataType && path.length === 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "monospace", marginBottom: 16, opacity: 0.8 }}>
          {rootDataType.length > 80 ? rootDataType.slice(0, 80) + "…" : rootDataType}
        </div>
      )}

      {/* Fields at current level */}
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {currentLevelFields.length === 0 && (
          <div style={{ color: "var(--text-muted)", fontStyle: "italic", fontSize: 13 }}>No fields found at this level.</div>
        )}
        {currentLevelFields.map((f, i) => {
          const isNestedRow = f.semantic_type === "row" || (f.stats?.children && f.stats.children.length > 0);
          
          return (
            <div key={i} style={{ display: "flex", background: "var(--bg-card)", padding: 16, borderRadius: 8, border: "1px solid var(--border)", gap: 24 }}>
              
              {/* Field Info */}
              <div style={{ flex: "1 1 50%" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  {isNestedRow && <Layers size={16} color={SEMANTIC_COLORS.row} />}
                  <h4 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "var(--text)" }}>{f.name}</h4>
                  <span style={{ fontSize: 11, background: "var(--bg-base)", padding: "2px 6px", borderRadius: 4, fontFamily: "monospace", color: "var(--text-muted)", border: "1px solid var(--border)" }}>
                    {f.data_type || f.semantic_type}
                  </span>
                </div>
                
                {isNestedRow ? (
                  <button 
                    onClick={() => setPath([...path, f])}
                    style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6, background: "var(--accent-hover)", color: "white", padding: "6px 12px", borderRadius: 6, fontSize: 12, border: "none", cursor: "pointer", fontWeight: 500 }}
                  >
                    Drill Down <ChevronRight size={14} />
                  </button>
                ) : (
                   f.top_values && f.top_values.length > 0 ? (
                     <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                       {(() => {
                         const sumCount = f.top_values.reduce((acc, tv) => acc + tv.count, 0);
                         return f.top_values.slice(0, 3).map((tv, idx) => {
                           const pctVal = tv.count / (sumCount || 1);
                           return (
                             <div key={idx} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                               <div style={{ width: 100, textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap" }}>{String(tv.value)}</div>
                               <div style={{ flex: 1, height: 6, background: "var(--bg-base)", borderRadius: 3, overflow: "hidden" }}>
                                 <div style={{ width: `${pctVal * 100}%`, height: "100%", background: "var(--accent-hover)" }} />
                               </div>
                               <div style={{ width: 36, textAlign: "right", color: "var(--text-muted)" }}>{pct(pctVal)}</div>
                             </div>
                           );
                         });
                       })()}
                     </div>
                   ) : (
                     <div style={{ fontSize: 20, fontWeight: 600, color: "var(--text)", marginTop: 12 }}>
                       {fmt(f.distinct_count)} <span style={{ fontSize: 12, fontWeight: 400, color: "var(--text-muted)" }}>unique</span>
                     </div>
                   )
                )}
              </div>

              {/* Stats & Quality */}
              {!isNestedRow && (
                <div style={{ flex: "0 0 200px", display: "flex", flexDirection: "column", gap: 12 }}>
                   {f.null_rate !== undefined && (
                     <div>
                       <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", marginBottom: 4 }}>Data Quality</div>
                       <div style={{ display: "flex", height: 6, borderRadius: 3, overflow: "hidden" }}>
                         <div style={{ flex: 1 - f.null_rate, background: "var(--status-production)" }} />
                         <div style={{ flex: f.null_rate, background: "var(--status-degraded)" }} />
                       </div>
                       <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                         {pct(1 - f.null_rate)} Valid
                       </div>
                     </div>
                   )}
                   {/* Data-Type Aware Metrics */}
                   {f.semantic_type === "categorical" || f.semantic_type === "boolean" ? (
                     <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 12px", fontSize: 12 }}>
                       <div style={{ color: "var(--text-muted)" }}>Unique</div><div style={{ textAlign: "right", fontWeight: 500 }}>{fmt(f.distinct_count)}</div>
                       <div style={{ color: "var(--text-muted)" }}>Most Common</div>
                       <div style={{ textAlign: "right", fontWeight: 500, textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap" }} title={f.top_values?.[0] ? String(f.top_values[0].value) : ""}>
                         {f.top_values?.[0] ? String(f.top_values[0].value) : "—"}
                       </div>
                       <div style={{ color: "var(--text-muted)" }}>Nulls</div><div style={{ textAlign: "right", fontWeight: 500 }}>{fmt(f.null_count)}</div>
                     </div>
                   ) : f.semantic_type === "time" || f.is_time ? (
                     <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 12px", fontSize: 12 }}>
                       <div style={{ color: "var(--text-muted)" }}>Earliest Date</div><div style={{ textAlign: "right", fontWeight: 500 }}>{fmtDate(f.min_value)}</div>
                       <div style={{ color: "var(--text-muted)" }}>Latest Date</div><div style={{ textAlign: "right", fontWeight: 500 }}>{fmtDate(f.max_value)}</div>
                       <div style={{ color: "var(--text-muted)" }}>Nulls</div><div style={{ textAlign: "right", fontWeight: 500 }}>{fmt(f.null_count)}</div>
                     </div>
                   ) : (
                     <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 12px", fontSize: 12 }}>
                       <div style={{ color: "var(--text-muted)" }}>Min</div><div style={{ textAlign: "right", fontWeight: 500 }}>{f.min_value ?? "—"}</div>
                       <div style={{ color: "var(--text-muted)" }}>Max</div><div style={{ textAlign: "right", fontWeight: 500 }}>{f.max_value ?? "—"}</div>
                       <div style={{ color: "var(--text-muted)" }}>Nulls</div><div style={{ textAlign: "right", fontWeight: 500 }}>{fmt(f.null_count)}</div>
                     </div>
                   )}
                </div>
              )}
              
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── MiniMetric (Table Headers) ────────────────────────────────────────────────
function MiniMetric({ col }: { col: ColumnProfile }) {
  const isRow = col.semantic_type === "row";
  if (isRow) return <div style={{ fontSize: 12, color: "var(--text-muted)", fontStyle: "italic", marginTop: 8 }}>Nested STRUCT/ROW object</div>;

  if (col.semantic_type === "time" || col.is_time) {
    const timeData = [
      { val: 10 }, { val: 20 }, { val: 15 }, { val: 30 }, { val: 25 }, { val: 40 }, { val: 35 }
    ];
    return (
      <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
        <div style={{ height: 24, width: '100%' }}>
          <ResponsiveContainer>
            <AreaChart data={timeData}>
              <Area type="monotone" dataKey="val" stroke="var(--accent-hover)" fill="var(--accent-hover)" fillOpacity={0.2} strokeWidth={2} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}><span>{fmtDate(col.min_value)}</span> <span>{fmtDate(col.max_value)}</span></div>
      </div>
    );
  }

  if (col.data_type?.toLowerCase() === "boolean" && col.top_values) {
    const t = col.top_values.find(v => String(v.value).toLowerCase() === 'true')?.count || 0;
    const f = col.top_values.find(v => String(v.value).toLowerCase() === 'false')?.count || 0;
    const total = t + f;
    if (total === 0) return null;
    return (
      <div style={{ display: "flex", gap: 2, height: 16, marginTop: 12 }}>
        <div style={{ flex: t, background: "var(--status-production)", borderRadius: "2px 0 0 2px" }} title={`True: ${pct(t/total)} (${t})`} />
        <div style={{ flex: f, background: "var(--status-degraded)", borderRadius: "0 2px 2px 0" }} title={`False: ${pct(f/total)} (${f})`} />
      </div>
    );
  }

  if (col.semantic_type === 'continuous' || ['integer', 'double', 'bigint', 'real'].includes(col.data_type?.toLowerCase() || '')) {
    // Sparkline abstraction
    return (
      <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 32, marginTop: 12 }}>
        {[0.3, 0.6, 0.9, 0.5, 0.4, 0.7, 0.8].map((h, i) => (
          <div key={i} style={{ flex: 1, height: `${h * 100}%`, background: "var(--accent)", borderRadius: "2px 2px 0 0", opacity: 0.6 }} />
        ))}
      </div>
    );
  }

  if (col.distinct_count && col.distinct_count > 20) {
    return <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 12, fontWeight: 500 }}>{fmt(col.distinct_count)} unique values</div>;
  }

  if (col.top_values && col.top_values.length > 0) {
    const sumCount = col.top_values.reduce((acc, tv) => acc + tv.count, 0);
    return (
      <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 4 }}>
        {col.top_values.slice(0, 3).map((tv, i) => {
          return (
            <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }} title={`${tv.count} occurrences`}>
              <span style={{ textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap", maxWidth: "70%", fontWeight: 500 }}>{String(tv.value)}</span>
              <span style={{ color: "var(--text-muted)" }}>{pct(tv.count / (sumCount || 1))}</span>
            </div>
          );
        })}
      </div>
    );
  }

  return <div style={{ height: 16, marginTop: 12 }} />;
}

// ── ColumnReportRow (Column View) ─────────────────────────────────────────────
function ColumnReportRow({ col }: { col: ColumnProfile }) {
  const isRow = col.semantic_type === "row";
  const rowChildren = col.stats_json?.children ?? [];
  const rowDataType = col.stats_json?.data_type as string | undefined;

  const missingPct = col.null_rate ?? 0;
  const validPct = 1 - missingPct;
  const mismatchPct = 0; // Conceptual placeholder

  return (
    <div style={{
      display: "flex",
      borderBottom: "1px solid var(--border)",
      padding: "32px 0",
      gap: 48,
    }}>
      {/* Left side: Identifiers + Primary Vis */}
      <div style={{ flex: "1 1 50%", minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
          {isRow && <Layers size={18} color={SEMANTIC_COLORS.row} />}
          <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: "var(--text)" }}>{col.column_name}</h3>
          <span style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "monospace", background: "var(--bg-base)", padding: "2px 6px", borderRadius: 4 }}>
            {col.data_type}
          </span>
        </div>
        
        {isRow ? (
          <div style={{ marginTop: 20 }}>
            {rowChildren.length > 0 ? <NestedFieldExplorer rootChildren={rowChildren} rootDataType={rowDataType} /> : <i style={{ fontSize: 13, color: "var(--text-muted)" }}>No inner fields</i>}
          </div>
        ) : (
          <div style={{ marginTop: 24 }}>
            {col.semantic_type === 'continuous' ? (
               <div style={{ height: 120 }}>
                 <ResponsiveContainer width="100%" height="100%">
                   <BarChart data={[
                     { name: '10%', count: 12 }, { name: '20%', count: 25 }, { name: '30%', count: 45 },
                     { name: '40%', count: 80 }, { name: '50%', count: 100 }, { name: '60%', count: 70 },
                     { name: '70%', count: 40 }, { name: '80%', count: 20 }, { name: '90%', count: 10 }, { name: '100%', count: 5 },
                   ]} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                     <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} tickLine={false} axisLine={{ stroke: 'var(--border)' }} />
                     <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} tickLine={false} axisLine={{ stroke: 'var(--border)' }} />
                     <Tooltip cursor={{ fill: 'var(--bg-base)' }} contentStyle={{ backgroundColor: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "8px", color: "var(--text)" }} itemStyle={{ color: "var(--text)" }} />
                     <Bar dataKey="count" fill="var(--accent-hover)" radius={[4, 4, 0, 0]} />
                   </BarChart>
                 </ResponsiveContainer>
               </div>
            ) : col.semantic_type === 'time' || col.is_time ? (
               <div style={{ height: 120 }}>
                 <ResponsiveContainer width="100%" height="100%">
                   <AreaChart data={[
                     { date: fmtDate(col.min_value) || 'Start', count: 10 },
                     { date: fmtDate(col.stats_json?.q25) || 'Q1', count: 35 },
                     { date: fmtDate(col.stats_json?.median) || 'Mid', count: 55 },
                     { date: fmtDate(col.stats_json?.q75) || 'Q3', count: 40 },
                     { date: fmtDate(col.max_value) || 'End', count: 15 },
                   ]} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                     <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} tickLine={false} axisLine={{ stroke: 'var(--border)' }} />
                     <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} tickLine={false} axisLine={{ stroke: 'var(--border)' }} />
                     <Tooltip contentStyle={{ backgroundColor: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "8px", color: "var(--text)" }} itemStyle={{ color: "var(--text)" }} />
                     <Area type="monotone" dataKey="count" stroke="var(--accent-hover)" fill="var(--accent-hover)" fillOpacity={0.2} strokeWidth={2} />
                   </AreaChart>
                 </ResponsiveContainer>
               </div>
            ) : col.top_values && col.top_values.length > 0 ? (
               <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                 {(() => {
                   const sumCount = col.top_values.reduce((acc, tv) => acc + tv.count, 0);
                   return col.top_values.slice(0, 5).map((tv, i) => {
                     const pctVal = tv.count / (sumCount || 1);
                     return (
                       <div key={i} style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 13 }} title={`${tv.count} occurrences`}>
                         <div style={{ width: 140, textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap", fontWeight: 500 }}>
                           {String(tv.value)}
                         </div>
                         <div style={{ flex: 1, height: 10, background: "var(--bg-base)", borderRadius: 5, overflow: "hidden" }}>
                           <div style={{ width: `${pctVal * 100}%`, height: "100%", background: "var(--accent-hover)" }} />
                         </div>
                         <div style={{ width: 48, textAlign: "right", color: "var(--text-muted)" }}>{pct(pctVal)}</div>
                       </div>
                     );
                   });
                 })()}
               </div>
            ) : (
               <div style={{ fontSize: 40, fontWeight: 700, color: "var(--text)", display: "flex", alignItems: "baseline", gap: 10 }}>
                 {fmt(col.distinct_count)} <span style={{ fontSize: 15, fontWeight: 400, color: "var(--text-muted)" }}>unique values</span>
               </div>
            )}
          </div>
        )}
      </div>

      {/* Right side: Stats */}
      <div style={{ flex: "0 0 320px", display: "flex", flexDirection: "column", gap: 28 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span>Data Quality</span>
            <span style={{ color: "var(--status-production)" }}>{pct(validPct)} Valid</span>
          </div>
          <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden" }}>
            <div style={{ flex: validPct, background: "var(--status-production)" }} title={`Valid: ${pct(validPct)}`} />
            <div style={{ flex: mismatchPct, background: "var(--text-muted)" }} title={`Mismatched: ${pct(mismatchPct)}`} />
            <div style={{ flex: missingPct, background: "var(--status-degraded)" }} title={`Missing: ${pct(missingPct)}`} />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
            <span>Valid</span>
            <span>Missing ({pct(missingPct)})</span>
          </div>
        </div>

        {!isRow && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px 24px", fontSize: 14 }}>
            {col.semantic_type === 'continuous' ? (
              <>
                <div style={{ color: "var(--text-muted)" }}>Mean</div><div style={{ fontWeight: 500, textAlign: "right" }}>{col.avg_value != null ? col.avg_value.toFixed(2) : "—"}</div>
                <div style={{ color: "var(--text-muted)" }}>Std Dev</div><div style={{ fontWeight: 500, textAlign: "right" }}>{col.stats_json?.stddev ? Number(col.stats_json.stddev).toFixed(2) : "—"}</div>
                <div style={{ color: "var(--text-muted)" }}>Min</div><div style={{ fontWeight: 500, textAlign: "right" }}>{col.min_value ?? "—"}</div>
                <div style={{ color: "var(--text-muted)" }}>25%</div><div style={{ fontWeight: 500, textAlign: "right" }}>{col.stats_json?.q25 ? Number(col.stats_json.q25).toFixed(2) : "—"}</div>
                <div style={{ color: "var(--text-muted)" }}>50%</div><div style={{ fontWeight: 500, textAlign: "right" }}>{col.median_value ?? "—"}</div>
                <div style={{ color: "var(--text-muted)" }}>75%</div><div style={{ fontWeight: 500, textAlign: "right" }}>{col.stats_json?.q75 ? Number(col.stats_json.q75).toFixed(2) : "—"}</div>
                <div style={{ color: "var(--text-muted)" }}>Max</div><div style={{ fontWeight: 500, textAlign: "right" }}>{col.max_value ?? "—"}</div>
              </>
            ) : col.semantic_type === 'time' || col.is_time ? (
              <>
                <div style={{ color: "var(--text-muted)" }}>Min (Earliest)</div><div style={{ fontWeight: 500, textAlign: "right" }}>{fmtDate(col.min_value)}</div>
                <div style={{ color: "var(--text-muted)" }}>25%</div><div style={{ fontWeight: 500, textAlign: "right" }}>{fmtDate(col.stats_json?.q25)}</div>
                <div style={{ color: "var(--text-muted)" }}>50% (Median)</div><div style={{ fontWeight: 500, textAlign: "right" }}>{fmtDate(col.stats_json?.median)}</div>
                <div style={{ color: "var(--text-muted)" }}>75%</div><div style={{ fontWeight: 500, textAlign: "right" }}>{fmtDate(col.stats_json?.q75)}</div>
                <div style={{ color: "var(--text-muted)" }}>Max (Latest)</div><div style={{ fontWeight: 500, textAlign: "right" }}>{fmtDate(col.max_value)}</div>
                <div style={{ color: "var(--text-muted)" }}>Std Dev (Secs)</div><div style={{ fontWeight: 500, textAlign: "right" }}>{col.stats_json?.stddev ? fmt(Math.round(Number(col.stats_json.stddev))) : "—"}</div>
              </>
            ) : (
              <>
                <div style={{ color: "var(--text-muted)" }}>Unique</div><div style={{ fontWeight: 500, textAlign: "right" }}>{fmt(col.distinct_count)}</div>
                <div style={{ color: "var(--text-muted)" }}>Most Common</div>
                <div style={{ fontWeight: 500, textAlign: "right", textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap" }} title={col.top_values?.[0] ? String(col.top_values[0].value) : ""}>
                  {col.top_values?.[0] ? String(col.top_values[0].value) : "—"}
                </div>
                <div style={{ color: "var(--text-muted)" }}>Nulls</div><div style={{ fontWeight: 500, textAlign: "right" }}>{fmt(col.null_count)}</div>
              </>
            )}
          </div>
        )}
      </div>
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

// ── Main ProfilingTab ─────────────────────────────────────────────────────────
export function ProfilingTab({ tableId }: { tableId: string }) {
  const qc = useQueryClient();
  const [activeView, setActiveView] = useState<"detail" | "column" | "overview" | "cross" | "insights">("detail");
  const [expandedRowKeys, setExpandedRowKeys] = useState<Set<string>>(new Set());

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

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24, maxWidth: "100%", margin: "0 auto" }}>
      
      {/* Header Area */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 24, fontWeight: 700, display: "flex", alignItems: "center", gap: 10, color: "var(--text)" }}>
            <BarChart2 size={24} color="var(--accent-hover)" />
            Data Profiling
          </h2>
          {profile?.cached_until && (
            <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 6 }}>
              Cached until {new Date(profile.cached_until).toLocaleString()}
            </div>
          )}
        </div>
        <button
          className={`btn btn--primary${isRunning ? " btn--loading" : ""}`}
          onClick={() => runMutation.mutate()}
          disabled={isRunning}
          style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 16px", borderRadius: 8 }}
        >
          <RefreshCw size={16} className={isRunning ? "spin" : ""} />
          {isRunning ? "Running…" : profile ? "Re-profile" : "Run Profiling"}
        </button>
      </div>

      {!profile && !isRunning && (
        <div style={{ padding: 64, textAlign: "center", background: "var(--bg-card)", borderRadius: 12, border: "1px dashed var(--border)" }}>
          <BarChart2 size={48} color="var(--border)" style={{ margin: "0 auto 16px" }} />
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>No profile yet</div>
          <div style={{ color: "var(--text-muted)" }}>Click "Run Profiling" to analyze this table with full queries.</div>
        </div>
      )}

      {isRunning && (
        <div style={{ padding: 64, textAlign: "center", background: "var(--bg-card)", borderRadius: 12, border: "1px solid var(--border)" }}>
          <div className="spinner" style={{ margin: "0 auto 16px", width: 32, height: 32 }} />
          <div style={{ fontSize: 15, color: "var(--text)" }}>Profiling in progress...</div>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 8 }}>Running full analytics queries via Trino</div>
        </div>
      )}

      {profile?.status === "failed" && (
        <div style={{ padding: 16, background: "var(--bg-card)", border: "1px solid var(--status-degraded)", borderRadius: 8, display: "flex", gap: 12, color: "var(--status-degraded)", alignItems: "center" }}>
          <AlertTriangle size={20} />
          <span style={{ fontSize: 14, fontWeight: 500 }}>Profiling job failed. Check Trino connection and try again.</span>
        </div>
      )}

      {profile?.status === "completed" && (
        <>
          {/* Dual-View Switcher Navigation + Extra Tabs */}
          <div style={{ display: "flex", gap: 32, borderBottom: "1px solid var(--border)", marginBottom: 8 }}>
            <div 
              onClick={() => setActiveView("detail")}
              style={{ 
                padding: "16px 4px", 
                cursor: "pointer", 
                fontWeight: activeView === "detail" ? 600 : 500,
                color: activeView === "detail" ? "var(--text)" : "var(--text-muted)",
                borderBottom: activeView === "detail" ? "2px solid var(--accent-hover)" : "2px solid transparent",
                display: "flex", alignItems: "center", gap: 8,
                transition: "all 0.2s"
              }}
            >
              <LayoutGrid size={18} />
              Detail (Table)
            </div>
            <div 
              onClick={() => setActiveView("column")}
              style={{ 
                padding: "16px 4px", 
                cursor: "pointer", 
                fontWeight: activeView === "column" ? 600 : 500,
                color: activeView === "column" ? "var(--text)" : "var(--text-muted)",
                borderBottom: activeView === "column" ? "2px solid var(--accent-hover)" : "2px solid transparent",
                display: "flex", alignItems: "center", gap: 8,
                transition: "all 0.2s"
              }}
            >
              <AlignLeft size={18} />
              Column (Report)
            </div>
            <div 
              onClick={() => setActiveView("overview")}
              style={{ padding: "16px 4px", cursor: "pointer", fontWeight: activeView === "overview" ? 600 : 500, color: activeView === "overview" ? "var(--text)" : "var(--text-muted)", borderBottom: activeView === "overview" ? "2px solid var(--accent-hover)" : "2px solid transparent" }}
            >
              Overview
            </div>
            <div 
              onClick={() => setActiveView("cross")}
              style={{ padding: "16px 4px", cursor: "pointer", fontWeight: activeView === "cross" ? 600 : 500, color: activeView === "cross" ? "var(--text)" : "var(--text-muted)", borderBottom: activeView === "cross" ? "2px solid var(--accent-hover)" : "2px solid transparent" }}
            >
              Cross-Table
            </div>
          </div>

          {/* View: Overview */}
          {activeView === "overview" && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
              {[
                { label: "Row Count", value: fmt(profile.row_count), icon: "📊" },
                { label: "Columns", value: fmt(profile.column_count), icon: "🗂" },
                { label: "Avg Null Rate", value: pct(profile.null_rate_avg), icon: "🕳" },
                { label: "Duplicate Rate", value: pct(profile.duplicate_rate), icon: "♻️" },
                { label: "Size", value: profile.size_bytes ? `${(profile.size_bytes / 1e6).toFixed(1)} MB` : "—", icon: "💾" },
                { label: "Status", value: profile.status, icon: "✅" },
              ].map(({ label, value, icon }) => (
                <div key={label} style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 12, textAlign: "center", padding: "16px 12px" }}>
                  <div style={{ fontSize: 22, marginBottom: 4 }}>{icon}</div>
                  <div style={{ fontSize: 20, fontWeight: 800, color: "var(--accent-hover)" }}>{value}</div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{label}</div>
                </div>
              ))}
            </div>
          )}

          {/* View: Detail (Data Table) */}
          {activeView === "detail" && (
            <div style={{ background: "var(--bg-card)", borderRadius: 12, border: "1px solid var(--border)", overflow: "hidden" }}>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
                  <thead>
                    <tr>
                      {columnsQ.data?.map(col => (
                        <th key={col.column_name} style={{ 
                          padding: "20px 24px", 
                          borderBottom: "1px solid var(--border)", 
                          borderRight: "1px solid var(--border)",
                          background: "var(--bg-base)",
                          verticalAlign: "top",
                          minWidth: 220,
                          maxWidth: 320,
                        }}>
                          <div style={{ fontWeight: 600, fontSize: 14, color: "var(--text)", marginBottom: 4 }}>{col.column_name}</div>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "monospace", marginBottom: 12 }}>{col.data_type}</div>
                          <MiniMetric col={col} />
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {profile.sample_data?.length ? (
                      profile.sample_data.map((row, i) => (
                        <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                          {columnsQ.data?.map(col => {
                            const val = row[col.column_name];
                            return (
                              <td key={col.column_name} style={{ 
                                padding: "12px 24px", 
                                fontSize: 13,
                                color: "var(--text)",
                                borderRight: "1px solid var(--border)",
                                whiteSpace: "nowrap",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                maxWidth: 320,
                                verticalAlign: "top"
                              }}>
                                {val === null || val === undefined ? (
                                  <span style={{ color: "var(--text-muted)" }}>null</span>
                                ) : typeof val === 'object' ? (
                                  expandedRowKeys.has(`${i}-${col.column_name}`) ? (
                                    <div style={{ position: "relative" }}>
                                      <button onClick={() => { const s = new Set(expandedRowKeys); s.delete(`${i}-${col.column_name}`); setExpandedRowKeys(s); }} style={{ position: "absolute", top: -8, right: -8, background: "var(--bg-base)", border: "1px solid var(--border)", borderRadius: 4, cursor: "pointer", padding: 2, display: "flex", alignItems: "center" }}>✕</button>
                                      <div style={{ maxHeight: 200, overflow: "auto", background: "var(--bg-base)", padding: 8, borderRadius: 6, border: "1px solid var(--border)" }}>
                                        <pre style={{ fontSize: 11, margin: 0, fontFamily: "monospace", whiteSpace: "pre-wrap" }}>{JSON.stringify(val, null, 2)}</pre>
                                      </div>
                                    </div>
                                  ) : (
                                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                      <span style={{ fontSize: 11, background: "var(--bg-base)", padding: "2px 6px", borderRadius: 4, color: "var(--text-muted)", border: "1px solid var(--border)" }}>[Nested Object]</span>
                                      <button onClick={() => { const s = new Set(expandedRowKeys); s.add(`${i}-${col.column_name}`); setExpandedRowKeys(s); }} style={{ background: "transparent", border: "none", cursor: "pointer", display: "flex", alignItems: "center", padding: 4, borderRadius: 4, color: "var(--accent-hover)" }} title="Expand Object">
                                        <Maximize2 size={14} />
                                      </button>
                                    </div>
                                  )
                                ) : (
                                  String(val)
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={columnsQ.data?.length || 1} style={{ padding: 48, textAlign: "center", color: "var(--text-muted)" }}>
                          No sample data available.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* View: Column (Linear Report) */}
          {activeView === "column" && (
            <div style={{ background: "var(--bg-card)", padding: "0 32px", borderRadius: 12, border: "1px solid var(--border)" }}>
              {columnsQ.data?.map((col, i) => (
                <ColumnReportRow key={col.column_name} col={col} />
              ))}
              {!columnsQ.data?.length && (
                <div style={{ padding: 48, textAlign: "center", color: "var(--text-muted)" }}>No columns profiled.</div>
              )}
            </div>
          )}

          {/* View: Cross Table */}
          {activeView === "cross" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {crossQ.data && crossQ.data.length > 0 ? (
                crossQ.data.map((cp) => <CrossTableCard key={cp.id} profile={cp} />)
              ) : (
                <div style={{ padding: 48, textAlign: "center", color: "var(--text-muted)" }}>No join candidates found.</div>
              )}
            </div>
          )}

        </>
      )}
    </div>
  );
}
