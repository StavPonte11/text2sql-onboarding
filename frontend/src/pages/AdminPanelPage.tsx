import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { adminApi } from '../api/admin';
import { useAdminStore } from '../store/adminStore';
import { ShieldCheck, CheckCircle2, XCircle, LogOut } from 'lucide-react';
import { message, Modal, Input } from 'antd';
import { useNavigate } from 'react-router-dom';
import { StatusBadge } from '../components/common/StatusBadge';

export function AdminPanelPage() {
  const { user, logout } = useAdminStore();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [rejectModalOpen, setRejectModalOpen] = useState(false);
  const [rejectNote, setRejectNote] = useState('');
  const [selectedTableId, setSelectedTableId] = useState<string | null>(null);

  const { data: pendingTables = [], isLoading, error } = useQuery({
    queryKey: ['admin', 'pendingTables'],
    queryFn: adminApi.getPendingTables,
    refetchInterval: 10000,
  });

  const approveMutation = useMutation({
    mutationFn: adminApi.approveTable,
    onSuccess: () => {
      message.success('Table approved for production!');
      queryClient.invalidateQueries({ queryKey: ['admin', 'pendingTables'] });
      queryClient.invalidateQueries({ queryKey: ['tables'] });
    },
    onError: (error: any) => {
      message.error(error.message || 'Failed to approve table');
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) => adminApi.rejectTable(id, note),
    onSuccess: () => {
      message.success('Table rejected and returned to sandbox');
      setRejectModalOpen(false);
      setRejectNote('');
      setSelectedTableId(null);
      queryClient.invalidateQueries({ queryKey: ['admin', 'pendingTables'] });
      queryClient.invalidateQueries({ queryKey: ['tables'] });
    },
    onError: (error: any) => {
      message.error(error.message || 'Failed to reject table');
    },
  });

  const handleLogout = () => {
    logout();
    navigate('/admin/login');
  };

  const handleRejectClick = (tableId: string) => {
    setSelectedTableId(tableId);
    setRejectNote('');
    setRejectModalOpen(true);
  };

  const submitReject = () => {
    if (selectedTableId && rejectNote.trim()) {
      rejectMutation.mutate({ id: selectedTableId, note: rejectNote });
    } else {
      message.warning('Please provide a rejection reason');
    }
  };

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <ShieldCheck size={28} color="var(--accent)" />
            <h1 className="page__title">Admin Approval Center</h1>
          </div>
          <p className="page__subtitle">Review verified tables before production promotion</p>
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div style={{ textAlign: 'right' }}>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Logged in as</p>
            <p style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>{user?.username}</p>
          </div>
          <button 
            onClick={handleLogout}
            className="btn btn--ghost btn--sm"
            title="Sign out"
          >
            <LogOut size={14} />
            Sign out
          </button>
        </div>
      </div>

      <div className="card">
        {isLoading ? (
          <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-secondary)' }}>
            Loading verified tables...
          </div>
        ) : error ? (
          <div style={{ padding: '40px', textAlign: 'center', color: 'var(--status-degraded)' }}>
            Failed to load pending tables
          </div>
        ) : pendingTables.length === 0 ? (
          <div className="empty-state">
            <CheckCircle2 size={48} className="empty-state__icon" />
            <div className="empty-state__text">All caught up!</div>
            <div className="empty-state__sub">There are no tables waiting for approval right now.</div>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Table Name</th>
                  <th>Schema</th>
                  <th>Status</th>
                  <th>Eval Score</th>
                  <th>Pass Rate</th>
                  <th>Evaluated Date</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {pendingTables.map((table: any) => (
                  <tr key={table.id}>
                    <td style={{ fontWeight: 600 }}>{table.name}</td>
                    <td>{table.schema_name}</td>
                    <td><StatusBadge status={table.status} /></td>
                    <td>
                      {table.latest_run ? (
                        <span style={{ color: table.latest_run.score >= 0.5 ? 'var(--status-production)' : 'var(--status-degraded)', fontWeight: 600 }}>
                          {(table.latest_run.score * 100).toFixed(0)}%
                        </span>
                      ) : '-'}
                    </td>
                    <td>
                      {table.latest_run ? (
                        <span style={{ color: table.latest_run.pass_rate >= 0.5 ? 'var(--status-production)' : 'var(--status-degraded)', fontWeight: 600 }}>
                          {(table.latest_run.pass_rate * 100).toFixed(0)}%
                        </span>
                      ) : '-'}
                    </td>
                    <td>
                      {table.latest_run ? new Date(table.latest_run.created_at).toLocaleDateString() : '-'}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                        <button
                          onClick={() => handleRejectClick(table.id)}
                          disabled={approveMutation.isPending || rejectMutation.isPending}
                          className="btn btn--danger btn--sm"
                        >
                          <XCircle size={14} /> Reject
                        </button>
                        <button
                          onClick={() => approveMutation.mutate(table.id)}
                          disabled={approveMutation.isPending || rejectMutation.isPending}
                          className="btn btn--primary btn--sm"
                        >
                          <CheckCircle2 size={14} /> Approve
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Modal
        title="Reject Table Promotion"
        open={rejectModalOpen}
        onOk={submitReject}
        onCancel={() => setRejectModalOpen(false)}
        confirmLoading={rejectMutation.isPending}
        okText="Reject & Return to Sandbox"
        okButtonProps={{ danger: true }}
      >
        <div style={{ marginBottom: '16px' }}>
          Please provide a reason for rejecting this promotion. The table will be moved back to the sandbox.
        </div>
        <Input.TextArea
          rows={4}
          placeholder="E.g., Query latency is too high, needs more index coverage..."
          value={rejectNote}
          onChange={(e) => setRejectNote(e.target.value)}
        />
      </Modal>
    </div>
  );
}
