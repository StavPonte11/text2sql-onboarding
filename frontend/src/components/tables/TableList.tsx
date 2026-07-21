import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { App } from 'antd';
import dayjs from 'dayjs';
import { Database, Plus, Search, Wand2 } from 'lucide-react';
import { z } from 'zod';

import { tablesApi } from '../../api/client';
import { ErrorState } from '../common/ErrorState';
import { SkeletonTable } from '../common/Skeleton';
import { StatusBadge } from '../common/StatusBadge';

import type { Table, TableStatus } from '../../types';

import './TableList.css';

const STATUS_OPTIONS: Array<{ value: TableStatus | ''; label: string }> = [
  { value: '', label: 'All Statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'sandbox', label: 'Sandbox' },
  { value: 'verified', label: 'Verified' },
  { value: 'production', label: 'Production' },
  { value: 'degraded', label: 'Degraded' },
];

const createSchema = z.object({
  oasis_source_id: z.string().trim().min(1, 'Oasis Source ID is required'),
});

type CreateSchemaType = z.infer<typeof createSchema>;

export function TableList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { message } = App.useApp();

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<TableStatus | ''>('');
  const [showCreate, setShowCreate] = useState(false);

  const {
    register,
    handleSubmit,
    reset,
    watch,
    setError,
    formState: { errors },
  } = useForm<CreateSchemaType>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      oasis_source_id: '',
    },
  });

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['tables', search, statusFilter],
    queryFn: () =>
      tablesApi.list({
        search: search || undefined,
        status: statusFilter || undefined,
      }),
  });

  const createMutation = useMutation({
    mutationFn: tablesApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tables'] });
      setShowCreate(false);
      reset();
      message.success('Table created successfully');
    },
  });

  const watchOasisSourceId = watch('oasis_source_id');
  const { reset: resetMutation } = createMutation;
  useEffect(() => {
    resetMutation();
  }, [watchOasisSourceId, resetMutation]);

  const onSubmit = (formData: CreateSchemaType) => {
    const allTables = qc.getQueryData<Table[]>(['tables', '', '']) || data || [];
    const isDuplicate = allTables.some(
      (t) =>
        t.oasis_source_id === formData.oasis_source_id ||
        `${t.service}.${t.catalog}.${t.schema_name}.${t.name}` === formData.oasis_source_id ||
        `${t.catalog}.${t.schema_name}.${t.name}` === formData.oasis_source_id
    );

    if (isDuplicate) {
      setError('oasis_source_id', {
        type: 'manual',
        message: 'This table already exists in the system.',
      });
      return;
    }

    createMutation.mutate(formData);
  };

  const handleCloseCreate = () => {
    setShowCreate(false);
    reset();
    createMutation.reset();
  };

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">{t('tables.title')}</h1>
          <p className="page__subtitle">Manage the lifecycle of TextToSQL tables</p>
        </div>
        <div className="flex gap-2">
          <button className="btn btn--ghost" onClick={() => navigate('/wizard')}>
            <Wand2 size={15} /> Onboard Table
          </button>
          <button className="btn btn--primary" onClick={() => setShowCreate(true)}>
            <Plus size={15} /> {t('tables.add')}
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="table-list__filters">
        <div className="table-list__search-container">
          <Search size={14} className="table-list__search-icon" />
          <input
            className="form-input table-list__search-input"
            placeholder={t('tables.searchPlaceholder')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="form-select table-list__status-select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as TableStatus | '')}
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
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
            <div className="empty-state__text">{t('tables.noData')}</div>
            <div className="empty-state__sub">Click "Add Table" to get started</div>
          </div>
        </div>
      ) : (
        <div className="card table-list__card">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('tables.cols.name')}</th>
                <th>{t('tables.cols.service', 'Service')}</th>
                <th>{t('tables.cols.catalog', 'Catalog')}</th>
                <th>{t('tables.cols.schema')}</th>
                <th>{t('tables.cols.status')}</th>
                <th>{t('tables.cols.owner')}</th>
                <th>{t('tables.cols.updated')}</th>
                <th>{t('tables.cols.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {data.map((table) => (
                <tr key={table.id}>
                  <td>
                    <span className="table-name-cell">{table.name}</span>
                  </td>
                  <td>
                    <code className="table-schema-code">{table.service}</code>
                  </td>
                  <td>
                    <code className="table-schema-code">{table.catalog}</code>
                  </td>
                  <td>
                    <code className="table-schema-code">{table.schema_name}</code>
                  </td>
                  <td>
                    <StatusBadge status={table.status} />
                  </td>
                  <td className="table-owner-cell">{table.owner_id}</td>
                  <td className="table-updated-cell">
                    {dayjs(table.updated_at).format('MMM D, YYYY HH:mm')}
                  </td>
                  <td>
                    <button
                      className="btn btn--ghost btn--sm"
                      onClick={() => navigate(`/tables/${table.id}`)}
                    >
                      {t('common.view')}
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
        <div className="modal-overlay" onClick={handleCloseCreate}>
          <form
            className="modal"
            onClick={(e) => e.stopPropagation()}
            onSubmit={handleSubmit(onSubmit)}
          >
            <h2 className="modal__title">Create New Table</h2>

            {createMutation.isError && (
              <div
                style={{
                  padding: '10px 12px',
                  borderRadius: 'var(--radius-sm)',
                  background: 'rgba(239, 68, 68, 0.08)',
                  color: 'var(--status-degraded)',
                  border: '1px solid rgba(239, 68, 68, 0.2)',
                  fontSize: '13px',
                  marginBottom: '16px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                }}
              >
                {(createMutation.error as any)?.response?.data?.detail ||
                  createMutation.error?.message ||
                  'Failed to create table'}
              </div>
            )}

            <div className="form-group">
              <label className="form-label">Oasis Source ID</label>
              <input
                className={`form-input${errors.oasis_source_id ? ' form-input--error' : ''}`}
                placeholder="e.g. some-uuid-or-fqn"
                {...register('oasis_source_id')}
              />
              {errors.oasis_source_id && (
                <div className="form-error">{errors.oasis_source_id.message}</div>
              )}
            </div>
            <div className="modal__actions">
              <button type="button" className="btn btn--ghost" onClick={handleCloseCreate}>
                {t('common.cancel')}
              </button>
              <button
                type="submit"
                className="btn btn--primary"
                disabled={createMutation.isPending || createMutation.isError}
              >
                {createMutation.isPending ? 'Creating...' : 'Create Table'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
