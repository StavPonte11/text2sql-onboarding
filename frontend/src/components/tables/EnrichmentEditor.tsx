import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { App } from 'antd';
import { Popover, Select, Tooltip } from 'antd';
import {
  AlignLeft,
  ChevronDown,
  ChevronRight,
  Clock,
  MapPin,
  RefreshCw,
  Table2,
} from 'lucide-react';
import { ArrowRight, Database, Link2, Unlink } from 'lucide-react';

import { enrichmentApi, tablesApi } from '../../api/client';
import { foreignKeysApi } from '../../api/client';
import { SkeletonCard } from '../common/Skeleton';

import type { EnrichmentData } from '../../types';
import type { ColumnDef, ForeignKeyMapping, Table } from '../../types';

const ForeignKeySelector = ({
  tableId,
  sourceColumn,
  currentMapping,
  allTables,
}: {
  tableId: string;
  sourceColumn: string;
  currentMapping?: ForeignKeyMapping;
  allTables: Table[];
}) => {
  const [targetTableId, setTargetTableId] = useState(currentMapping?.target_table_id || '');
  const [targetColumn, setTargetColumn] = useState(currentMapping?.target_column || '');
  const [isOpen, setIsOpen] = useState(false);

  const qc = useQueryClient();
  const { message } = App.useApp();

  const { data: targetEnrichment } = useQuery({
    queryKey: ['enrichment', targetTableId],
    queryFn: () => enrichmentApi.getLatest(targetTableId),
    enabled: !!targetTableId,
  });

  const createMutation = useMutation({
    mutationFn: (payload: any) => foreignKeysApi.create(tableId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['foreignKeys', tableId] });
      message.success('Foreign key saved');
    },
    onError: () => message.error('Failed to save foreign key'),
  });

  const deleteMutation = useMutation({
    mutationFn: () => foreignKeysApi.delete(tableId, currentMapping!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['foreignKeys', tableId] });
      message.success('Foreign key removed');
      setTargetTableId('');
      setTargetColumn('');
    },
    onError: () => message.error('Failed to remove foreign key'),
  });

  // Sync state if currentMapping changes from outside
  useEffect(() => {
    setTargetTableId(currentMapping?.target_table_id || '');
    setTargetColumn(currentMapping?.target_column || '');
  }, [currentMapping]);

  const columns = targetEnrichment?.data?.columns || [];
  const flattenCols = (cols: ColumnDef[]): ColumnDef[] => {
    return cols.flatMap((c) => [c, ...(c.children ? flattenCols(c.children) : [])]);
  };
  const allTargetColumns = flattenCols(columns);

  const handleSave = (tTable: string, tCol: string) => {
    if (tTable && tCol) {
      createMutation.mutate({
        source_column: sourceColumn,
        target_table_id: tTable,
        target_column: tCol,
      });
      setIsOpen(false);
    }
  };

  const popoverContent = (
    <div style={{ padding: '4px', display: 'flex', flexDirection: 'column', gap: 12, width: 220 }}>
      <div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: 'var(--text-muted)',
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            marginBottom: 6,
          }}
        >
          Target Table
        </div>
        <Select
          size="middle"
          style={{ width: '100%' }}
          placeholder="Select table..."
          value={targetTableId || undefined}
          onChange={(val) => {
            setTargetTableId(val);
            setTargetColumn('');
          }}
          allowClear
          options={allTables.map((t) => ({ label: t.name, value: t.id }))}
        />
      </div>
      <div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: 'var(--text-muted)',
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            marginBottom: 6,
          }}
        >
          Target Column
        </div>
        <Select
          size="middle"
          style={{ width: '100%' }}
          placeholder="Select column..."
          value={targetColumn || undefined}
          disabled={!targetTableId}
          onChange={(val) => {
            setTargetColumn(val);
            handleSave(targetTableId, val);
          }}
          options={allTargetColumns.map((c) => ({ label: c.name, value: c.name }))}
        />
      </div>
    </div>
  );

  if (currentMapping) {
    const targetTableName =
      allTables.find((t) => t.id === currentMapping.target_table_id)?.name ||
      currentMapping.target_table_id;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Popover
          content={popoverContent}
          trigger="click"
          open={isOpen}
          onOpenChange={setIsOpen}
          placement="bottomLeft"
          overlayInnerStyle={{
            borderRadius: 8,
            padding: 12,
            boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1)',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 10px',
              background: 'var(--primary-light, rgba(59, 130, 246, 0.1))',
              color: 'var(--primary, #3b82f6)',
              borderRadius: 6,
              fontSize: 12,
              fontWeight: 500,
              cursor: 'pointer',
              transition: 'all 0.2s ease',
              border: '1px solid var(--primary-border, rgba(59, 130, 246, 0.2))',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background =
                'var(--primary-light-hover, rgba(59, 130, 246, 0.15))';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'var(--primary-light, rgba(59, 130, 246, 0.1))';
            }}
          >
            <Database size={12} />
            <span>{targetTableName}</span>
            <ArrowRight size={10} style={{ opacity: 0.6 }} />
            <span>{currentMapping.target_column}</span>
          </div>
        </Popover>
        <Tooltip title="Remove Relation">
          <button
            onClick={() => deleteMutation.mutate()}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 24,
              height: 24,
              borderRadius: 4,
              border: 'none',
              background: 'transparent',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              transition: 'all 0.2s ease',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--danger, #ef4444)';
              e.currentTarget.style.background = 'var(--danger-light, rgba(239, 68, 68, 0.1))';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = 'var(--text-muted)';
              e.currentTarget.style.background = 'transparent';
            }}
          >
            <Unlink size={13} />
          </button>
        </Tooltip>
      </div>
    );
  }

  return (
    <Popover
      content={popoverContent}
      trigger="click"
      open={isOpen}
      onOpenChange={setIsOpen}
      placement="bottomLeft"
      overlayInnerStyle={{
        borderRadius: 8,
        padding: 12,
        boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1)',
      }}
    >
      <button
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '4px 10px',
          background: 'transparent',
          border: '1px dashed var(--border)',
          borderRadius: 6,
          color: 'var(--text-muted)',
          fontSize: 12,
          fontWeight: 500,
          cursor: 'pointer',
          transition: 'all 0.2s ease',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = 'var(--primary, #3b82f6)';
          e.currentTarget.style.color = 'var(--primary, #3b82f6)';
          e.currentTarget.style.background = 'var(--primary-light, rgba(59, 130, 246, 0.05))';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = 'var(--border)';
          e.currentTarget.style.color = 'var(--text-muted)';
          e.currentTarget.style.background = 'transparent';
        }}
      >
        <Link2 size={12} />
        Add Relation
      </button>
    </Popover>
  );
};

const ColumnRow = ({
  col,
  depth = 0,
  tableId,
  foreignKeys,
  allTables,
}: {
  col: ColumnDef;
  depth?: number;
  tableId: string;
  foreignKeys: ForeignKeyMapping[];
  allTables: Table[];
}) => {
  const [expanded, setExpanded] = useState(false);
  const hasChildren = col.children && col.children.length > 0;

  const currentMapping = foreignKeys.find((fk) => fk.source_column === col.name);

  return (
    <>
      <tr>
        <td style={{ paddingLeft: depth * 20 + 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            {hasChildren ? (
              <button
                onClick={() => setExpanded(!expanded)}
                style={{
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  padding: 0,
                  display: 'flex',
                  alignItems: 'center',
                  color: 'var(--text-muted)',
                }}
              >
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
            ) : (
              <span style={{ width: 14, display: 'inline-block' }}></span>
            )}
            <span
              style={{
                fontFamily: 'monospace',
                fontSize: 13,
                fontWeight: 600,
                background: 'var(--bg-subtle, rgba(0,0,0,0.05))',
                padding: '2px 7px',
                borderRadius: 4,
                color: 'var(--text)',
              }}
            >
              {col.name || '—'}
            </span>
            {col.dataType && (
              <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 5 }}>
                {col.dataType}
              </span>
            )}
          </div>
        </td>
        <td
          style={{
            fontSize: 13,
            color: col.description ? 'var(--text)' : 'var(--text-muted)',
            fontStyle: col.description ? 'normal' : 'italic',
          }}
        >
          {col.description || 'No description'}
        </td>
        <td>
          <ForeignKeySelector
            tableId={tableId}
            sourceColumn={col.name}
            currentMapping={currentMapping}
            allTables={allTables}
          />
        </td>
        <td>
          <div style={{ display: 'flex', gap: 5, justifyContent: 'center' }}>
            {col.is_geo && (
              <span
                className="badge badge--neutral"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  fontSize: 11,
                  padding: '2px 8px',
                }}
              >
                <MapPin size={10} /> Geo
              </span>
            )}
            {col.is_time && (
              <span
                className="badge badge--neutral"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  fontSize: 11,
                  padding: '2px 8px',
                }}
              >
                <Clock size={10} /> Time
              </span>
            )}
            {!col.is_geo && !col.is_time && (
              <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>—</span>
            )}
          </div>
        </td>
      </tr>
      {expanded &&
        hasChildren &&
        col.children!.map((child, idx) => (
          <ColumnRow
            key={`${child.name}-${idx}`}
            col={child}
            depth={depth + 1}
            tableId={tableId}
            foreignKeys={foreignKeys}
            allTables={allTables}
          />
        ))}
    </>
  );
};

interface Props {
  tableId: string;
}

export function EnrichmentEditor({ tableId }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { message } = App.useApp();

  const { data, isLoading } = useQuery({
    queryKey: ['enrichment', tableId],
    queryFn: () => enrichmentApi.getLatest(tableId),
    retry: false,
  });

  const { data: allTablesData } = useQuery({
    queryKey: ['tables'],
    queryFn: () => tablesApi.list(),
  });

  const { data: foreignKeysData } = useQuery({
    queryKey: ['foreignKeys', tableId],
    queryFn: () => foreignKeysApi.list(tableId),
  });

  const syncMutation = useMutation({
    mutationFn: () => tablesApi.syncSchema(tableId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['enrichment', tableId] });
      qc.invalidateQueries({ queryKey: ['table', tableId] });
      message.success('Schema synced successfully from OpenMetadata');
    },
    onError: (err: any) => {
      message.error(err.response?.data?.detail || 'Failed to sync schema');
    },
  });

  const [schema, setSchema] = useState<EnrichmentData>({ table_description: '', columns: [] });

  useEffect(() => {
    if (data?.data) setSchema(data.data);
  }, [data]);

  const allTables = allTablesData || [];
  const foreignKeys = foreignKeysData || [];

  if (isLoading) return <SkeletonCard />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 style={{ fontSize: 17, fontWeight: 700 }}>Schema</h2>
        <button
          className="btn btn--ghost btn--sm"
          disabled={syncMutation.isPending}
          onClick={() => syncMutation.mutate()}
          style={{
            fontSize: 11,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            height: 28,
            padding: '0 10px',
          }}
        >
          <RefreshCw size={12} className={syncMutation.isPending ? 'animate-spin' : ''} />
          {syncMutation.isPending ? 'Syncing...' : 'Sync Schema'}
        </button>
      </div>

      {/* Table Description */}
      <div className="card" style={{ padding: '18px 20px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            marginBottom: 10,
            color: 'var(--text-muted)',
            fontSize: 12,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          <AlignLeft size={13} />
          Table Description
        </div>
        <p
          style={{
            fontSize: 14,
            lineHeight: 1.65,
            color: schema.table_description ? 'var(--text)' : 'var(--text-muted)',
            fontStyle: schema.table_description ? 'normal' : 'italic',
            margin: 0,
          }}
        >
          {schema.table_description || 'No description provided.'}
        </p>
      </div>

      {/* Columns */}
      <div className="card" style={{ padding: '18px 20px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            marginBottom: 16,
            color: 'var(--text-muted)',
            fontSize: 12,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          <Table2 size={13} />
          {t('enrichment.columns')} ({schema.columns.length})
        </div>

        {schema.columns.length === 0 ? (
          <div className="empty-state" style={{ padding: '28px 0' }}>
            <div className="empty-state__text">No columns defined</div>
            <div className="empty-state__sub">No schema information available</div>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table" style={{ width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: 180 }}>Column</th>
                  <th>Description</th>
                  <th style={{ width: 300 }}>Foreign Key</th>
                  <th style={{ width: 100, textAlign: 'center' }}>Tags</th>
                </tr>
              </thead>
              <tbody>
                {schema.columns.map((col, i) => (
                  <ColumnRow
                    key={i}
                    col={col}
                    tableId={tableId}
                    foreignKeys={foreignKeys}
                    allTables={allTables}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
