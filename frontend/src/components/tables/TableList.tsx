import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus, Search, Database, Wand2 } from "lucide-react";
import { App } from "antd";
import { tablesApi } from "../../api/client";
import type { TableCreate, TableStatus } from "../../types";
import { StatusBadge } from "../common/StatusBadge";
import { SkeletonTable } from "../common/Skeleton";
import { ErrorState } from "../common/ErrorState";
import dayjs from "dayjs";
import "./TableList.css";

const STATUS_OPTIONS: Array<{ value: TableStatus | ""; label: string }> = [
  { value: "", label: "All Statuses" },
  { value: "draft", label: "Draft" },
  { value: "sandbox", label: "Sandbox" },
  { value: "verified", label: "Verified" },
  { value: "production", label: "Production" },
  { value: "degraded", label: "Degraded" },
];

export function TableList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { message } = App.useApp();

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<TableStatus | "">("");
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<TableCreate>({
    oasis_source_id: "",
  });

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["tables", search, statusFilter],
    queryFn: () => tablesApi.list({
      search: search || undefined,
      status: statusFilter || undefined,
    }),
  });

  const createMutation = useMutation({
    mutationFn: tablesApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tables"] });
      setShowCreate(false);
      setCreateForm({ oasis_source_id: "" });
      message.success("Table created successfully");
    },
  });

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">{t("tables.title")}</h1>
          <p className="page__subtitle">Manage the lifecycle of TextToSQL tables</p>
        </div>
        <div className="flex gap-2">
          <button className="btn btn--ghost" onClick={() => navigate("/wizard")}>
            <Wand2 size={15} /> Onboard Table
          </button>
          <button className="btn btn--primary" onClick={() => setShowCreate(true)}>
            <Plus size={15} /> {t("tables.add")}
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="table-list__filters">
        <div className="table-list__search-container">
          <Search size={14} className="table-list__search-icon" />
          <input
            className="form-input table-list__search-input"
            placeholder={t("tables.searchPlaceholder")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="form-select table-list__status-select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as TableStatus | "")}
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      {isLoading ? (
        <SkeletonTable rows={6} cols={5} />
      ) : isError ? (
        <ErrorState onRetry={refetch} />
      ) : !data?.length ? (
        <div className="card">
          <div className="empty-state">
            <Database size={40} className="empty-state__icon" />
            <div className="empty-state__text">{t("tables.noData")}</div>
            <div className="empty-state__sub">Click "Add Table" to get started</div>
          </div>
        </div>
      ) : (
        <div className="card table-list__card">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("tables.cols.name")}</th>
                <th>{t("tables.cols.service", "Service")}</th>
                <th>{t("tables.cols.catalog", "Catalog")}</th>
                <th>{t("tables.cols.schema")}</th>
                <th>{t("tables.cols.status")}</th>
                <th>{t("tables.cols.owner")}</th>
                <th>{t("tables.cols.updated")}</th>
                <th>{t("tables.cols.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {data.map((table) => (
                <tr key={table.id}>
                  <td>
                    <span className="table-name-cell">
                      {table.name}
                    </span>
                  </td>
                  <td><code className="table-schema-code">{table.service}</code></td>
                  <td><code className="table-schema-code">{table.catalog}</code></td>
                  <td><code className="table-schema-code">{table.schema_name}</code></td>
                  <td><StatusBadge status={table.status} /></td>
                  <td className="table-owner-cell">{table.owner_id}</td>
                  <td className="table-updated-cell">
                    {dayjs(table.updated_at).format("MMM D, YYYY HH:mm")}
                  </td>
                  <td>
                    <button
                      className="btn btn--ghost btn--sm"
                      onClick={() => navigate(`/tables/${table.id}`)}
                    >
                      {t("common.view")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal__title">Create New Table</h2>
            <div className="form-group">
              <label className="form-label">Oasis Source ID</label>
              <input
                className="form-input"
                placeholder="e.g. some-uuid-or-fqn"
                value={createForm.oasis_source_id}
                onChange={(e) => setCreateForm({ oasis_source_id: e.target.value })}
              />
            </div>
            <div className="modal__actions">
              <button className="btn btn--ghost" onClick={() => setShowCreate(false)}>
                {t("common.cancel")}
              </button>
              <button
                className="btn btn--primary"
                disabled={!createForm.oasis_source_id || createMutation.isPending}
                onClick={() => createMutation.mutate(createForm)}
              >
                {createMutation.isPending ? "Creating..." : "Create Table"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
