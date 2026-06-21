import React from 'react';
import { Card, Tag, Table, Typography, Space } from 'antd';
import { Database, Filter, Link, ListOrdered, Sparkles } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import styles from './SchemaPlanDisplay.module.css';

const { Text } = Typography;

interface SchemaPlanDisplayProps {
  planString: string;
}

const renderCellSafe = (val: any): string => {
  if (val === null || val === undefined) return '';
  if (typeof val === 'object') {
    return val.column_name || val.name || JSON.stringify(val);
  }
  return String(val);
};

export const SchemaPlanDisplay: React.FC<SchemaPlanDisplayProps> = ({ planString }) => {
  let planData: any = null;
  try {
    planData = JSON.parse(planString);
  } catch (e) {
    // If it's not JSON, render it as markdown
    return (
      <div className="markdown-body" style={{ fontSize: 13, color: 'var(--text-muted)' }}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{planString}</ReactMarkdown>
      </div>
    );
  }

  const explanationText = planData.description || planData.explanation || planData.strategy || planData.reasoning || planData.logic;

  // Define columns for Tables
  const tableColumns = [
    {
      title: 'Table Name',
      key: 'name',
      render: (record: any) => {
        const name = record.name || record.table_name || record.tableName || record.table || '';
        return <Text strong style={{ color: 'var(--primary)' }}>{renderCellSafe(name)}</Text>;
      },
    },
    {
      title: 'Columns',
      key: 'columns',
      render: (record: any) => {
        const columns = record.columns || record.column_names || record.columnNames || [];
        if (!columns || columns.length === 0) {
          return (
            <span style={{ color: 'var(--text-muted)', fontStyle: 'italic', fontSize: '12px' }}>
              No columns selected / empty
            </span>
          );
        }
        return (
          <Space size={[0, 4]} wrap>
            {columns?.map((col: any, idx: number) => {
              if (typeof col === 'string') {
                return (
                  <Tag key={col} color="blue" bordered={false}>
                    {col}
                  </Tag>
                );
              } else if (col && typeof col === 'object') {
                const name = col.column_name || col.name || `col_${idx}`;
                const type = col.data_type || col.type || '';
                return (
                  <Tag key={`${name}_${idx}`} color="blue" bordered={false}>
                    {name} {type ? <span style={{ opacity: 0.6, fontSize: '11px' }}>({type})</span> : ''}
                  </Tag>
                );
              }
              return null;
            })}
          </Space>
        );
      },
    },
  ];

  // Define columns for Joins
  const joinColumns = [
    {
      title: 'Source Table',
      key: 'source_table',
      render: (record: any) => {
        const val = record.source_table || record.sourceTable || record.srcTable || record.src_table || '';
        return <Tag color="cyan">{renderCellSafe(val)}</Tag>;
      }
    },
    {
      title: 'Source Column',
      key: 'source_column',
      render: (record: any) => {
        const val = record.source_column || record.sourceColumn || record.srcColumn || record.src_column || '';
        return renderCellSafe(val);
      }
    },
    {
      title: 'Target Table',
      key: 'target_table',
      render: (record: any) => {
        const val = record.target_table || record.targetTable || record.destTable || record.dest_table || record.tgtTable || record.tgt_table || '';
        return <Tag color="geekblue">{renderCellSafe(val)}</Tag>;
      }
    },
    {
      title: 'Target Column',
      key: 'target_column',
      render: (record: any) => {
        const val = record.target_column || record.targetColumn || record.destColumn || record.dest_column || record.tgtColumn || record.tgt_column || '';
        return renderCellSafe(val);
      }
    },
    {
      title: 'Join Type',
      key: 'type',
      render: (record: any) => {
        const val = record.type || record.join_type || record.joinType || 'INNER';
        return <Text code>{renderCellSafe(val).toUpperCase()}</Text>;
      }
    }
  ];

  // Define columns for Filters
  const filterColumns = [
    {
      title: 'Column',
      key: 'column',
      render: (record: any) => {
        const val = record.column || record.column_name || record.columnName || record.col || '';
        return <Text strong>{renderCellSafe(val)}</Text>;
      }
    },
    {
      title: 'Operator',
      key: 'operator',
      render: (record: any) => {
        const val = record.operator || record.op || '';
        return <Tag color="orange">{renderCellSafe(val)}</Tag>;
      }
    },
    {
      title: 'Value',
      key: 'value',
      render: (record: any) => {
        const val = record.value || record.val || '';
        return <Text code>{renderCellSafe(val)}</Text>;
      }
    }
  ];

  return (
    <div className={styles.schemaPlanContainer}>
      {explanationText && (
        <div className={styles.section} style={{ marginBottom: 20 }}>
          <Card size="small" className={styles.infoCard} style={{ borderLeft: '3px solid var(--primary)', background: 'rgba(22, 119, 255, 0.02)' }}>
            <div style={{ fontWeight: 600, marginBottom: 6, color: 'var(--text-h)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <Sparkles size={14} color="var(--primary)" />
              <span>Query Logic & Strategy</span>
            </div>
            <Text style={{ color: 'var(--text)', fontSize: '13px' }}>{renderCellSafe(explanationText)}</Text>
          </Card>
        </div>
      )}

      {planData.tables !== undefined && (
        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <Database size={16} /> <span>Tables & Columns</span>
          </div>
          <Table 
            dataSource={planData.tables} 
            columns={tableColumns} 
            rowKey={(r, i) => (r.name || r.table_name || r.tableName || r.table || i.toString())}
            pagination={false}
            size="small"
            bordered
            className={styles.dataTable}
          />
        </div>
      )}

      {planData.joins && planData.joins.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <Link size={16} /> <span>Joins</span>
          </div>
          <Table 
            dataSource={planData.joins} 
            columns={joinColumns} 
            rowKey={(r, i) => i.toString()}
            pagination={false}
            size="small"
            bordered
            className={styles.dataTable}
          />
        </div>
      )}

      {planData.filters && planData.filters.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <Filter size={16} /> <span>Filters</span>
          </div>
          <Table 
            dataSource={planData.filters} 
            columns={filterColumns} 
            rowKey={(r, i) => i.toString()}
            pagination={false}
            size="small"
            bordered
            className={styles.dataTable}
          />
        </div>
      )}

      {(planData.order_by?.length > 0 || planData.limit) && (
        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <ListOrdered size={16} /> <span>Sorting & Limits</span>
          </div>
          <Card size="small" className={styles.infoCard}>
            {planData.order_by?.length > 0 && (
              <div style={{ marginBottom: planData.limit ? 8 : 0 }}>
                <Text strong>Order By: </Text>
                {planData.order_by.map((ob: any, i: number) => (
                  <Tag key={i} color="purple">{renderCellSafe(ob.column)} {renderCellSafe(ob.direction)?.toUpperCase() || 'ASC'}</Tag>
                ))}
              </div>
            )}
            {planData.limit && (
              <div>
                <Text strong>Limit: </Text> <Tag color="magenta">{planData.limit}</Tag>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
};
