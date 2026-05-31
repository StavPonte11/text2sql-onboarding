import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, Mail, ShieldCheck } from 'lucide-react';

import { adminApi } from '../api/admin';
import { useAdminStore } from '../store/adminStore';

export function AdminLoginPage() {
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const setAuth = useAdminStore((state) => state.setAuth);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const user = await adminApi.login(email);

      if (!user.is_admin) {
        // Should not normally reach here — the backend already rejects non-admins
        // but guard on the client side too
        navigate('/control-center?no_permissions=1', { replace: true });
        return;
      }

      setAuth(user);
      navigate('/admin');
    } catch (err: any) {
      const msg: string = err.message || 'Login failed.';
      // If the error is a permissions error, redirect to control center with popup
      if (
        msg.toLowerCase().includes('permission') ||
        msg.toLowerCase().includes('forbidden') ||
        msg.toLowerCase().includes('admin')
      ) {
        navigate('/control-center?no_permissions=1', { replace: true });
        return;
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="layout" style={{ justifyContent: 'center', alignItems: 'center' }}>
      <div
        className="card card--elevated"
        style={{ width: '100%', maxWidth: '420px', padding: '40px' }}
      >
        <div style={{ textAlign: 'center', marginBottom: '28px' }}>
          <div
            style={{
              display: 'inline-flex',
              padding: '18px',
              background: 'linear-gradient(135deg, rgba(14,165,233,0.15), rgba(139,92,246,0.15))',
              borderRadius: '50%',
              marginBottom: '18px',
              border: '1px solid rgba(14,165,233,0.2)',
            }}
          >
            <ShieldCheck size={34} color="var(--accent)" />
          </div>
          <h2
            style={{
              fontSize: '22px',
              fontWeight: 700,
              color: 'var(--text-primary)',
              marginBottom: '8px',
            }}
          >
            Admin Access
          </h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
            Enter your email to access the admin panel
          </p>
        </div>

        {error && (
          <div
            style={{
              marginBottom: '20px',
              padding: '12px 14px',
              background: 'rgba(239,68,68,0.08)',
              border: '1px solid rgba(239,68,68,0.2)',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <AlertCircle size={15} color="#ef4444" style={{ flexShrink: 0 }} />
            <span style={{ fontSize: '13.5px', color: '#ef4444' }}>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div className="form-group" style={{ marginBottom: '24px' }}>
            <label className="form-label">
              <Mail size={13} style={{ marginRight: 5, verticalAlign: 'middle' }} />
              Email address
            </label>
            <input
              id="admin-email"
              type="email"
              className="form-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              required
              autoFocus
            />
          </div>

          <button
            id="admin-login-submit"
            type="submit"
            className="btn btn--primary"
            style={{
              width: '100%',
              justifyContent: 'center',
              padding: '11px 16px',
              fontSize: '14px',
            }}
            disabled={loading}
          >
            {loading ? 'Verifying…' : 'Sign In'}
          </button>
        </form>

        <p
          style={{
            marginTop: '20px',
            textAlign: 'center',
            fontSize: '12.5px',
            color: 'var(--text-muted)',
          }}
        >
          Access is restricted to users with admin privileges.
        </p>
      </div>
    </div>
  );
}
