import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { MapPin, Clock, AlignLeft, Table2, RefreshCw, ChevronRight, ChevronDown } from "lucide-react";
import { enrichmentApi, tablesApi } from "../../api/client";
import type { EnrichmentData } from "../../types";
import { SkeletonCard } from "../common/Skeleton";
import { App } from "antd";
import type { ColumnDef } from "../../types";

const ColumnRow = ({ col, depth = 0 }: { col: ColumnDef, depth?: number }) => {
  const [expanded, setExpanded] = useState(false);
  const hasChildren = col.children && col.children.length > 0;

  return (
    <>
      <tr>
        <td style={{ paddingLeft: depth * 20 + 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            {hasChildren ? (
              <button 
                onClick={() => setExpanded(!expanded)} 
                style={{ background: "none", border: "none", cursor: "pointer", padding: 0, display: "flex", alignItems: "center", color: "var(--text-muted)" }}
              >
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
            ) : (
              <span style={{ width: 14, display: "inline-block" }}></span>
            )}
            <span style={{
              fontFamily: "monospace",
              fontSize: 13,
              fontWeight: 600,
              background: "var(--bg-subtle, rgba(0,0,0,0.05))",
              padding: "2px 7px",
              borderRadius: 4,
              color: "var(--text)",
            }}>
              {col.name || "—"}
            </span>
            {col.dataType && (
              <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 5 }}>
                {col.dataType}
              </span>
            )}
          </div>
        </td>
        <td style={{ fontSize: 13, color: col.description ? "var(--text)" : "var(--text-muted)", fontStyle: col.description ? "normal" : "italic" }}>
          {col.description || "No description"}
        </td>
        <td>
          <div style={{ display: "flex", gap: 5, justifyContent: "center" }}>
            {col.is_geo && (
              <span className="badge badge--neutral" style={{
                display: "flex", alignItems: "center", gap: 4,
                fontSize: 11, padding: "2px 8px",
              }}>
                <MapPin size={10} /> Geo
              </span>
            )}
            {col.is_time && (
              <span className="badge badge--neutral" style={{
                display: "flex", alignItems: "center", gap: 4,
                fontSize: 11, padding: "2px 8px",
              }}>
                <Clock size={10} /> Time
              </span>
            )}
            {!col.is_geo && !col.is_time && (
              <span style={{ color: "var(--text-muted)", fontSize: 12 }}>—</span>
            )}
          </div>
        </td>
      </tr>
      {expanded && hasChildren && col.children!.map((child, idx) => (
        <ColumnRow key={`${child.name}-${idx}`} col={child} depth={depth + 1} />
      ))}
    </>
  );
};

interface Props { tableId: string }

export function EnrichmentEditor({ tableId }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { message } = App.useApp();

  const { data, isLoading } = useQuery({
    queryKey: ["enrichment", tableId],
    queryFn: () => enrichmentApi.getLatest(tableId),
    retry: false,
  });

  const syncMutation = useMutation({
    mutationFn: () => tablesApi.syncSchema(tableId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["enrichment", tableId] });
      qc.invalidateQueries({ queryKey: ["table", tableId] });
      message.success("Schema synced successfully from OpenMetadata");
    },
    onError: (err: any) => {
      message.error(err.response?.data?.detail || "Failed to sync schema");
    }
  });

  const [schema, setSchema] = useState<EnrichmentData>({ table_description: "", columns: [] });

  useEffect(() => {
    if (data?.data) setSchema(data.data);
  }, [data]);

  if (isLoading) return <SkeletonCard />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h2 style={{ fontSize: 17, fontWeight: 700 }}>Schema</h2>
        <button 
          className="btn btn--ghost btn--sm"
          disabled={syncMutation.isPending}
          onClick={() => syncMutation.mutate()}
          style={{ 
            fontSize: 11, 
            display: "flex", 
            alignItems: "center", 
            gap: 6,
            height: 28,
            padding: "0 10px"
          }}
        >
          <RefreshCw size={12} className={syncMutation.isPending ? "animate-spin" : ""} />
          {syncMutation.isPending ? "Syncing..." : "Sync Schema"}
        </button>
      </div>

      {/* Table Description */}
      <div className="card" style={{ padding: "18px 20px" }}>
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 7,
          marginBottom: 10,
          color: "var(--text-muted)",
          fontSize: 12,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
        }}>
          <AlignLeft size={13} />
          Table Description
        </div>
        <p style={{
          fontSize: 14,
          lineHeight: 1.65,
          color: schema.table_description ? "var(--text)" : "var(--text-muted)",
          fontStyle: schema.table_description ? "normal" : "italic",
          margin: 0,
        }}>
          {schema.table_description || "No description provided."}
        </p>
      </div>

      {/* Columns */}
      <div className="card" style={{ padding: "18px 20px" }}>
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 7,
          marginBottom: 16,
          color: "var(--text-muted)",
          fontSize: 12,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
        }}>
          <Table2 size={13} />
          {t("enrichment.columns")} ({schema.columns.length})
        </div>

        {schema.columns.length === 0 ? (
          <div className="empty-state" style={{ padding: "28px 0" }}>
            <div className="empty-state__text">No columns defined</div>
            <div className="empty-state__sub">No schema information available</div>
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="data-table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th style={{ width: 180 }}>Column</th>
                  <th>Description</th>
                  <th style={{ width: 100, textAlign: "center" }}>Tags</th>
                </tr>
              </thead>
              <tbody>
                {schema.columns.map((col, i) => (
                  <ColumnRow key={i} col={col} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
