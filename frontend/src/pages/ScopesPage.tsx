import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus, Shield, Check } from "lucide-react";
import { App } from "antd";
import { scopesApi } from "../api/client";
import type { UserScopeCreate } from "../types";
import { useAppStore } from "../store/appStore";
import { SkeletonCard } from "../components/common/Skeleton";
import { ErrorState } from "../components/common/ErrorState";

export function ScopesPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const { setActiveScope } = useAppStore();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<UserScopeCreate>({ user_id: "user-1", name: "" });

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["scopes"],
    queryFn: scopesApi.list,
  });

  const createMutation = useMutation({
    mutationFn: scopesApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scopes"] });
      setShowCreate(false);
      setForm({ user_id: "user-1", name: "" });
      message.success("Scope created successfully");
    },
  });

  const activateMutation = useMutation({
    mutationFn: scopesApi.activate,
    onSuccess: (scope) => {
      qc.invalidateQueries({ queryKey: ["scopes"] });
      setActiveScope(scope);
      message.success(`Scope "${scope.name}" activated`);
    },
  });

  if (isLoading) return <div className="page"><SkeletonCard /></div>;
  if (isError) return <div className="page"><ErrorState onRetry={refetch} /></div>;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">{t("scopes.title")}</h1>
          <p className="page__subtitle">Manage user table scopes and active context</p>
        </div>
        <button className="btn btn--primary" onClick={() => setShowCreate(true)}>
          <Plus size={15} /> {t("scopes.create")}
        </button>
      </div>

      {!data?.length ? (
        <div className="card">
          <div className="empty-state">
            <Shield size={36} className="empty-state__icon" />
            <div className="empty-state__text">{t("common.noData")}</div>
            <div className="empty-state__sub">Create a scope to limit which tables are visible to a user</div>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {data.map((scope) => (
            <div key={scope.id} className="card" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 15 }}>{scope.name}</div>
                <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginTop: 2 }}>
                  User: {scope.user_id} · ID: {scope.id}
                </div>
              </div>
              <div className="flex gap-2 items-center">
                {scope.is_active && (
                  <span style={{ color: "var(--status-production)", fontSize: 12, fontWeight: 700, display: "flex", alignItems: "center", gap: 4 }}>
                    <Check size={14} /> {t("scopes.active")}
                  </span>
                )}
                <button
                  className={`btn btn--sm ${scope.is_active ? "btn--ghost" : "btn--primary"}`}
                  disabled={scope.is_active || activateMutation.isPending}
                  onClick={() => activateMutation.mutate(scope.id)}
                >
                  {t("scopes.activate")}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal__title">{t("scopes.create")}</h2>
            <div className="form-group">
              <label className="form-label">Scope Name</label>
              <input className="form-input" value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Finance Tables" />
            </div>
            <div className="form-group">
              <label className="form-label">User ID</label>
              <input className="form-input" value={form.user_id}
                onChange={(e) => setForm((f) => ({ ...f, user_id: e.target.value }))} />
            </div>
            <div className="modal__actions">
              <button className="btn btn--ghost" onClick={() => setShowCreate(false)}>{t("common.cancel")}</button>
              <button className="btn btn--primary"
                disabled={!form.name || createMutation.isPending}
                onClick={() => createMutation.mutate(form)}>
                {createMutation.isPending ? "Creating..." : t("common.save")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
