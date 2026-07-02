import { Badge, Button, Popconfirm, Tag } from 'antd';
import { Edit3, Trash2, Zap } from 'lucide-react';
import { type ExecutionMode } from '../../api/flags';

export function ModeCard({
  mode,
  onEdit,
  onDelete,
}: {
  mode: ExecutionMode;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const overrideCount = Object.keys(mode.flag_overrides || {}).length;
  return (
    <div className={`mode-card ${!mode.is_active ? 'mode-card--inactive' : ''}`}>
      <div className="mode-card__header">
        <div className="mode-card__title">
          <Zap size={14} className="mode-card__icon" />
          <span>{mode.name}</span>
          {!mode.is_active && <Badge color="gray" text="Inactive" />}
        </div>
        <div className="mode-card__actions">
          <Button type="text" size="small" icon={<Edit3 size={14} />} onClick={onEdit} />
          <Popconfirm
            title={`Delete mode "${mode.name}"?`}
            description="Active sessions using this mode won't be affected until their next invocation."
            onConfirm={onDelete}
            okText="Delete"
            okButtonProps={{ danger: true }}
          >
            <Button type="text" size="small" danger icon={<Trash2 size={14} />} />
          </Popconfirm>
        </div>
      </div>
      <p className="mode-card__desc">{mode.description || <em>No description</em>}</p>
      <div className="mode-card__overrides">
        <span className="mode-card__override-count">{overrideCount} flag override{overrideCount !== 1 ? 's' : ''}</span>
        {Object.entries(mode.flag_overrides || {}).slice(0, 4).map(([k, v]) => (
          <Tag key={k} style={{ fontSize: 10 }}>{k}: {JSON.stringify(v)}</Tag>
        ))}
        {overrideCount > 4 && <Tag>+{overrideCount - 4} more</Tag>}
      </div>
      <div 
        className="mode-card__footer" 
        style={{ 
          marginTop: 12, 
          display: 'flex', 
          justifyContent: 'space-between', 
          alignItems: 'center', 
          fontSize: 10, 
          color: 'var(--color-text-secondary, #8c8c8c)', 
          borderTop: '1px solid var(--color-border-subtle, #f0f0f0)', 
          paddingTop: 8 
        }}
      >
        <span>Created by: <span style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>{mode.created_by || 'system'}</span></span>
        <span>Updated: {new Date(mode.updated_at).toLocaleDateString()}</span>
      </div>
    </div>
  );
}
