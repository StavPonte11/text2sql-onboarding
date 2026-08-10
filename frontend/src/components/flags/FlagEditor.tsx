import { useState } from 'react';
import { Button, Input, InputNumber, Popconfirm, Switch, Tag, Tooltip } from 'antd';
import { Edit3, RotateCcw, Save, X } from 'lucide-react';

import { type FeatureFlag } from '../../api/flags';
import { TYPE_COLORS } from '../../config/flagsConfig';

export function FlagEditor({
  flag,
  onSave,
  onReset,
  isSaving,
  isResetting,
}: {
  flag: FeatureFlag;
  onSave: (name: string, value: unknown) => void;
  onReset: (name: string) => void;
  isSaving: boolean;
  isResetting: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<unknown>(flag.value);

  const handleSave = () => {
    onSave(flag.name, draft);
    setEditing(false);
  };

  const handleCancel = () => {
    setDraft(flag.value);
    setEditing(false);
  };

  const displayValue = () => {
    if (flag.value === null || flag.value === undefined) {
      return <span className="flag-value--null">env default</span>;
    }
    if (flag.type === 'bool') {
      return (
        <Switch
          checked={flag.value as boolean}
          size="small"
          onChange={(v) => onSave(flag.name, v)}
          loading={isSaving}
        />
      );
    }
    return <code className="flag-value">{JSON.stringify(flag.value)}</code>;
  };

  const editor = () => {
    if (flag.type === 'bool') return null; // bool uses switch directly
    if (flag.type === 'int') {
      return (
        <InputNumber
          value={draft as number}
          step={1}
          precision={0}
          onChange={(v) => setDraft(v)}
          size="small"
          style={{ width: 120 }}
        />
      );
    }
    if (flag.type === 'float') {
      return (
        <InputNumber
          value={draft as number}
          step={0.01}
          onChange={(v) => setDraft(v)}
          size="small"
          style={{ width: 120 }}
        />
      );
    }
    if (flag.type === 'json') {
      return (
        <Input.TextArea
          value={typeof draft === 'string' ? draft : JSON.stringify(draft, null, 2)}
          onChange={(e) => {
            try {
              setDraft(JSON.parse(e.target.value));
            } catch {
              setDraft(e.target.value);
            }
          }}
          autoSize={{ minRows: 2, maxRows: 6 }}
          style={{ width: 280, fontFamily: 'monospace', fontSize: 12 }}
        />
      );
    }
    return (
      <Input
        value={draft as string}
        onChange={(e) => setDraft(e.target.value)}
        size="small"
        style={{ width: 200 }}
      />
    );
  };

  return (
    <div className="flag-row">
      <div className="flag-row__meta">
        <span className="flag-row__name">{flag.name}</span>
        <Tag color={TYPE_COLORS[flag.type]} style={{ fontSize: 10 }}>
          {flag.type}
        </Tag>
      </div>

      <div className="flag-row__desc">{flag.description}</div>

      <div className="flag-row__value">
        {editing && flag.type !== 'bool' ? (
          <div className="flag-row__edit-controls">
            {editor()}
            <Tooltip title="Save">
              <Button
                type="primary"
                size="small"
                icon={<Save size={12} />}
                onClick={handleSave}
                loading={isSaving}
              />
            </Tooltip>
            <Tooltip title="Cancel">
              <Button size="small" icon={<X size={12} />} onClick={handleCancel} />
            </Tooltip>
          </div>
        ) : (
          <div className="flag-row__display">
            {displayValue()}
            {flag.type !== 'bool' && (
              <Tooltip title="Edit value">
                <Button
                  type="text"
                  size="small"
                  icon={<Edit3 size={12} />}
                  onClick={() => {
                    setDraft(flag.value);
                    setEditing(true);
                  }}
                />
              </Tooltip>
            )}
          </div>
        )}
      </div>

      <div className="flag-row__actions">
        <span className="flag-row__owner">{flag.owner}</span>
        {flag.last_modified_by && flag.last_modified_by !== 'seed' && (
          <span className="flag-row__modifier">by {flag.last_modified_by}</span>
        )}
        <Popconfirm
          title={`Reset ${flag.name} to env default?`}
          description="This clears the DB override and falls back to the environment variable."
          onConfirm={() => onReset(flag.name)}
          okText="Reset"
          okButtonProps={{ danger: true }}
        >
          <Tooltip title="Reset to env default">
            <Button
              type="text"
              size="small"
              danger
              icon={<RotateCcw size={12} />}
              loading={isResetting}
              disabled={flag.value === null}
            />
          </Tooltip>
        </Popconfirm>
      </div>
    </div>
  );
}
