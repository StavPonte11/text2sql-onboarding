import { useTranslation } from 'react-i18next';
import { AlertTriangle, RefreshCw } from 'lucide-react';

interface Props {
  message?: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: Props) {
  const { t } = useTranslation();
  return (
    <div className="empty-state">
      <AlertTriangle size={36} className="empty-state__icon" color="var(--status-degraded)" />
      <div className="empty-state__text">{message ?? t('common.error')}</div>
      {onRetry && (
        <button className="btn btn--ghost btn--sm" onClick={onRetry}>
          <RefreshCw size={13} />
          {t('common.retry')}
        </button>
      )}
    </div>
  );
}
