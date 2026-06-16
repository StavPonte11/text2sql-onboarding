import { useState, useMemo } from 'react';
import {
  Badge,
  Button,
  Collapse,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Select,
  Switch,
  Tag,
  Tooltip,
} from 'antd';
import {
  AlertTriangle,
  ChevronDown,
  Edit3,
  Plus,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { flagsApi, type FeatureFlag, type ExecutionMode } from '../api/flags';
import { QUERY_KEYS } from '../config/constants';
import { useAdminStore } from '../store/adminStore';
import './FlagsPage.css';

// ── Flag category grouping ─────────────────────────────────────────────────

const FLAG_CATEGORIES: Record<string, string[]> = {
  Extraction: [
    'EXTRACTOR_MODEL', 'EXTRACTOR_TEMPERATURE', 'EXTRACTOR_TOP_K_TABLES', 'TABLE_SCOPING_MODE',
  ],
  'Schema Explorer': [
    'MAX_PROFILES_TO_FETCH', 'PROFILE_FETCH_CONCURRENCY', 'SCHEMA_CACHE_TTL', 'PROFILE_CACHE_TTL',
    'SCHEMA_SEMANTIC_TYPING', 'SCHEMA_JOIN_GRAPH', 'SCHEMA_SUMMARIZATION', 'SCHEMA_AMBIGUITY_DETECT',
    'SCHEMA_SUMMARY_MODEL', 'SCHEMA_TOP_K_JOINS',
  ],
  'Query Builder': ['QUERY_BUILDER_MODEL', 'QUERY_BUILDER_TEMPERATURE'],
  Refiner: ['MAX_REFINER_ITERATIONS', 'MAX_SCHEMA_REPLAN_ITERATIONS', 'REFINER_MODEL'],
  'Satisfaction Check': [
    'SATISFACTION_CHECK_ENABLED', 'SATISFACTION_CHECK_EXECUTION', 'SATISFACTION_CHECK_PLAUSIBILITY',
    'SATISFACTION_CHECK_COLUMNS', 'SATISFACTION_CHECK_SEMANTIC', 'SATISFACTION_MIN_ROWS',
    'SATISFACTION_MAX_ROWS', 'SATISFACTION_SEMANTIC_THRESHOLD', 'SATISFACTION_JUDGE_MODEL',
  ],
  Skills: ['SKILLS_ENABLED', 'SKILLS_HOT_RELOAD', 'SKILLS_CACHE_TTL'],
  Evaluation: ['LLM_JUDGE_ENABLED', 'EVAL_PARALLEL_WORKERS', 'EVAL_JUDGE_MODEL'],
  'Catalog Validation': ['CATALOG_VALIDATION_ENABLED', 'CATALOG_CACHE_TTL'],
};

const TYPE_COLORS: Record<string, string> = {
  bool: 'blue', int: 'green', float: 'cyan', string: 'purple', json: 'orange',
};

// ── Inline Flag Editor ─────────────────────────────────────────────────────

function FlagEditor({
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
            try { setDraft(JSON.parse(e.target.value)); } catch { setDraft(e.target.value); }
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
        <Tag color={TYPE_COLORS[flag.type]} style={{ fontSize: 10 }}>{flag.type}</Tag>
      </div>

      <div className="flag-row__desc">{flag.description}</div>

      <div className="flag-row__value">
        {editing && flag.type !== 'bool' ? (
          <div className="flag-row__edit-controls">
            {editor()}
            <Tooltip title="Save">
              <Button type="primary" size="small" icon={<Save size={12} />} onClick={handleSave} loading={isSaving} />
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
                  onClick={() => { setDraft(flag.value); setEditing(true); }}
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

// ── Execution Mode Card ────────────────────────────────────────────────────

function ModeCard({
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

export function FlagsPage() {
  const queryClient = useQueryClient();
  const user = useAdminStore((state) => state.user);
  
  const [activeTab, setActiveTab] = useState<TabKey>('flags');
  const [search, setSearch] = useState('');
  const [modeModalOpen, setModeModalOpen] = useState(false);
  const [editingMode, setEditingMode] = useState<ExecutionMode | null>(null);
  const [modeDraft, setModeDraft] = useState({ name: '', description: '', flag_overrides: '{}', is_active: true });
  const [savingFlags, setSavingFlags] = useState<Record<string, boolean>>({});
  const [resettingFlags, setResettingFlags] = useState<Record<string, boolean>>({});
  
  // Overrides list for the interactive builder
  const [overridesList, setOverridesList] = useState<{ name: string; value: any }[]>([]);
  const [selectedFlagToAdd, setSelectedFlagToAdd] = useState<string | null>(null);

  const { data: flags = [], isLoading: flagsLoading } = useQuery({
    queryKey: [QUERY_KEYS.FLAGS],
    queryFn: flagsApi.list,
    staleTime: 30_000,
  });

  const { data: modes = [], isLoading: modesLoading } = useQuery({
    queryKey: [QUERY_KEYS.EXECUTION_MODES],
    queryFn: flagsApi.listModes,
    staleTime: 30_000,
  });

  // Get all flags that are not yet overridden
  const availableFlags = useMemo(() => {
    const overriddenNames = new Set(overridesList.map(o => o.name));
    return flags.filter(f => !overriddenNames.has(f.name));
  }, [flags, overridesList]);

  const handleAddOverride = (flagName: string) => {
    const flagMeta = flags.find(f => f.name === flagName);
    if (!flagMeta) return;

    let defaultValue: any = '';
    if (flagMeta.type === 'bool') defaultValue = false;
    else if (flagMeta.type === 'int') defaultValue = 0;
    else if (flagMeta.type === 'float') defaultValue = 0.0;
    else if (flagMeta.type === 'json') defaultValue = {};

    setOverridesList([...overridesList, { name: flagName, value: defaultValue }]);
    setSelectedFlagToAdd(null);
  };

  const handleUpdateOverride = (name: string, value: any) => {
    setOverridesList(overridesList.map(item => item.name === name ? { ...item, value } : item));
  };

  const handleRemoveOverride = (name: string) => {
    setOverridesList(overridesList.filter(item => item.name !== name));
  };

  const updateFlagMutation = useMutation({
    mutationFn: ({ name, value }: { name: string; value: unknown }) => flagsApi.update(name, value),
    onMutate: ({ name }) => setSavingFlags((s) => ({ ...s, [name]: true })),
    onSettled: (_d, _e, { name }) => setSavingFlags((s) => ({ ...s, [name]: false })),
    onSuccess: (_, { name }) => {
      message.success(`Flag "${name}" updated`);
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.FLAGS] });
    },
    onError: (err: Error, { name }) => {
      message.error(`Failed to update "${name}": ${err.message}`);
    },
  });

  const resetFlagMutation = useMutation({
    mutationFn: (name: string) => flagsApi.reset(name),
    onMutate: (name) => setResettingFlags((s) => ({ ...s, [name]: true })),
    onSettled: (_d, _e, name) => setResettingFlags((s) => ({ ...s, [name]: false })),
    onSuccess: (_, name) => {
      message.success(`Flag "${name}" reset to env default`);
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.FLAGS] });
    },
    onError: (err: Error, name) => {
      message.error(`Failed to reset "${name}": ${err.message}`);
    },
  });

  const upsertModeMutation = useMutation({
    mutationFn: ({ name, data }: { name: string; data: object }) => flagsApi.upsertMode(name, data),
    onSuccess: () => {
      message.success('Execution mode saved');
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.EXECUTION_MODES] });
      setModeModalOpen(false);
    },
    onError: (err: Error) => message.error(`Failed to save mode: ${err.message}`),
  });

  const deleteModeMutation = useMutation({
    mutationFn: (name: string) => flagsApi.deleteMode(name),
    onSuccess: () => {
      message.success('Execution mode deleted');
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.EXECUTION_MODES] });
    },
    onError: (err: Error) => message.error(`Failed to delete mode: ${err.message}`),
  });

  // Group and filter flags
  const flagMap = useMemo(
    () => Object.fromEntries(flags.map((f) => [f.name, f])),
    [flags],
  );

  const filteredCategories = useMemo(() => {
    const q = search.toLowerCase();
    return Object.entries(FLAG_CATEGORIES).filter(([cat, names]) => {
      if (!q) return true;
      return cat.toLowerCase().includes(q) || names.some((n) => n.toLowerCase().includes(q));
    }).map(([cat, names]) => ({
      cat,
      flags: names
        .filter((n) => !q || n.toLowerCase().includes(q) || cat.toLowerCase().includes(q))
        .map((n) => flagMap[n])
        .filter(Boolean) as FeatureFlag[],
    })).filter((g) => g.flags.length > 0);
  }, [flags, flagMap, search]);

  const openNewMode = () => {
    setEditingMode(null);
    setModeDraft({ name: '', description: '', flag_overrides: '{}', is_active: true });
    setOverridesList([]);
    setModeModalOpen(true);
  };

  const openEditMode = (mode: ExecutionMode) => {
    setEditingMode(mode);
    setModeDraft({
      name: mode.name,
      description: mode.description,
      flag_overrides: JSON.stringify(mode.flag_overrides, null, 2),
      is_active: mode.is_active,
    });
    const parsed = mode.flag_overrides || {};
    const list = Object.entries(parsed).map(([name, value]) => ({ name, value }));
    setOverridesList(list);
    setModeModalOpen(true);
  };

  const submitMode = () => {
    // Construct overrides object from overridesList
    const overridesObj: Record<string, any> = {};
    for (const item of overridesList) {
      overridesObj[item.name] = item.value;
    }
    
    upsertModeMutation.mutate({
      name: modeDraft.name,
      data: { 
        description: modeDraft.description, 
        flag_overrides: overridesObj, 
        is_active: modeDraft.is_active 
      },
    });
  };

  const collapseItems = filteredCategories.map(({ cat, flags: catFlags }) => ({
    key: cat,
    label: (
      <div className="collapse-header">
        <span>{cat}</span>
        <Badge count={catFlags.length} showZero color="var(--color-accent-primary)" />
      </div>
    ),
    children: (
      <div className="flags-list">
        {catFlags.map((flag) => (
          <FlagEditor
            key={flag.name}
            flag={flag}
            onSave={(name, value) => updateFlagMutation.mutate({ name, value })}
            onReset={(name) => resetFlagMutation.mutate(name)}
            isSaving={!!savingFlags[flag.name]}
            isResetting={!!resettingFlags[flag.name]}
          />
        ))}
      </div>
    ),
  }));

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">
            <SlidersHorizontal size={20} />
            Feature Flags
          </h1>
          <p className="page__subtitle">
            Configure agent parameters and execution modes without redeployment.
            Changes take effect within 30 seconds.
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flags-tabs">
        <button
          className={`flags-tab ${activeTab === 'flags' ? 'flags-tab--active' : ''}`}
          onClick={() => setActiveTab('flags')}
        >
          Feature Flags
          <Badge count={flags.length} showZero color="var(--color-text-tertiary)" style={{ marginLeft: 6 }} />
        </button>
        <button
          className={`flags-tab ${activeTab === 'modes' ? 'flags-tab--active' : ''}`}
          onClick={() => setActiveTab('modes')}
        >
          Execution Modes
          <Badge count={modes.length} showZero color="var(--color-text-tertiary)" style={{ marginLeft: 6 }} />
        </button>
      </div>

      {/* ── FEATURE FLAGS TAB ─────────────────────────────────────────────── */}
      {activeTab === 'flags' && (
        <div className="flags-content">
          <div className="flags-toolbar">
            <Input
              placeholder="Search flags..."
              prefix={<ChevronDown size={14} />}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              allowClear
              style={{ maxWidth: 340 }}
            />
            <div className="flags-toolbar__info">
              <AlertTriangle size={13} />
              <span>Changes are audited and cached for 30s. Restart the agent process if you need immediate effect.</span>
            </div>
          </div>

          {flagsLoading ? (
            <div className="flags-loading">Loading flags…</div>
          ) : (
            <Collapse
              items={collapseItems}
              defaultActiveKey={filteredCategories.slice(0, 2).map((g) => g.cat)}
              ghost
              className="flags-collapse"
            />
          )}
        </div>
      )}

      {/* ── EXECUTION MODES TAB ───────────────────────────────────────────── */}
      {activeTab === 'modes' && (
        <div className="flags-content">
          <div className="flags-toolbar">
            <p className="modes-description">
              Execution modes are named sets of flag overrides. Pass <code>execution_mode="cost_saving"</code> to the MCP tool to activate a preset.
            </p>
            <Button
              type="primary"
              icon={<Plus size={14} />}
              onClick={openNewMode}
            >
              New Mode
            </Button>
          </div>

          {modesLoading ? (
            <div className="flags-loading">Loading modes…</div>
          ) : (
            <div className="modes-grid">
              {modes.map((mode) => (
                <ModeCard
                  key={mode.name}
                  mode={mode}
                  onEdit={() => openEditMode(mode)}
                  onDelete={() => deleteModeMutation.mutate(mode.name)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── MODE EDITOR MODAL ─────────────────────────────────────────────── */}
      <Modal
        title={editingMode ? `Edit Mode: ${editingMode.name}` : 'New Execution Mode'}
        open={modeModalOpen}
        onOk={submitMode}
        onCancel={() => setModeModalOpen(false)}
        okText="Save Mode"
        confirmLoading={upsertModeMutation.isPending}
        width={580}
      >
        <div className="mode-form" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {!editingMode && (
            <div className="mode-form__field">
              <label style={{ fontWeight: 500, display: 'block', marginBottom: 6 }}>Mode Name <span className="required" style={{ color: 'red' }}>*</span></label>
              <Input
                placeholder="e.g. my_experiment"
                value={modeDraft.name}
                onChange={(e) => setModeDraft((d) => ({ ...d, name: e.target.value }))}
              />
              <span className="mode-form__hint" style={{ fontSize: 11, color: 'var(--color-text-tertiary, #bfbfbf)', display: 'block', marginTop: 4 }}>Used as the value for execution_mode in MCP calls.</span>
            </div>
          )}
          <div className="mode-form__field">
            <label style={{ fontWeight: 500, display: 'block', marginBottom: 6 }}>Description</label>
            <Input.TextArea
              rows={2}
              value={modeDraft.description}
              onChange={(e) => setModeDraft((d) => ({ ...d, description: e.target.value }))}
              placeholder="What is this mode for?"
            />
          </div>
          
          <div className="mode-form__field" style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
            <div style={{ flex: 1 }}>
              <label style={{ fontWeight: 500, display: 'block', marginBottom: 6 }}>Created By</label>
              <Input
                value={editingMode ? editingMode.created_by : (user?.email || 'admin@company.com')}
                disabled
                style={{ cursor: 'not-allowed' }}
              />
            </div>
            <div style={{ width: 100, display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
              <label style={{ fontWeight: 500, display: 'block', marginBottom: 6 }}>Active</label>
              <Switch
                checked={modeDraft.is_active}
                onChange={(v) => setModeDraft((d) => ({ ...d, is_active: v }))}
              />
            </div>
          </div>

          <div className="mode-form__field" style={{ borderTop: '1px solid var(--color-border-subtle)', paddingTop: 16 }}>
            <label style={{ fontWeight: 600, display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <span>Configuration Overrides</span>
              <span className="mode-form__hint">{overridesList.length} override(s) configured</span>
            </label>
            
            <div className="overrides-builder">
              {overridesList.length > 0 ? (
                <div className="overrides-builder-list">
                  {overridesList.map((item) => {
                    const flagMeta = flags.find(f => f.name === item.name);
                    const flagType = flagMeta ? flagMeta.type : 'string';
                    const flagDesc = flagMeta ? flagMeta.description : '';
                    
                    return (
                      <div key={item.name} className="override-builder-item">
                        <div className="override-builder-item__info">
                          <Tooltip title={flagDesc}>
                            <span className="override-builder-item__name">{item.name}</span>
                          </Tooltip>
                          <span className="override-builder-item__type">
                            Type: <Tag color={TYPE_COLORS[flagType]} style={{ fontSize: 9, lineHeight: '14px', height: 16 }}>{flagType}</Tag>
                          </span>
                        </div>
                        
                        <div className="override-builder-item__value">
                          {flagType === 'bool' && (
                            <Switch 
                              checked={!!item.value} 
                              onChange={(v) => handleUpdateOverride(item.name, v)} 
                            />
                          )}
                          {flagType === 'int' && (
                            <InputNumber 
                              value={item.value as number} 
                              step={1} 
                              precision={0} 
                              onChange={(v) => handleUpdateOverride(item.name, v)}
                              style={{ width: '100%', maxWidth: 140 }} 
                            />
                          )}
                          {flagType === 'float' && (
                            <InputNumber 
                              value={item.value as number} 
                              step={0.1} 
                              onChange={(v) => handleUpdateOverride(item.name, v)}
                              style={{ width: '100%', maxWidth: 140 }} 
                            />
                          )}
                          {flagType === 'string' && (
                            <Input 
                              value={item.value as string} 
                              onChange={(e) => handleUpdateOverride(item.name, e.target.value)} 
                              style={{ width: '100%', maxWidth: 160 }}
                            />
                          )}
                          {flagType === 'json' && (
                            <Input.TextArea 
                              rows={2}
                              value={typeof item.value === 'string' ? item.value : JSON.stringify(item.value, null, 2)} 
                              onChange={(e) => {
                                let parsedVal;
                                try {
                                  parsedVal = JSON.parse(e.target.value);
                                } catch {
                                  parsedVal = e.target.value;
                                }
                                handleUpdateOverride(item.name, parsedVal);
                              }}
                              style={{ fontFamily: 'monospace', fontSize: 11, width: '100%', maxWidth: 180 }}
                            />
                          )}
                        </div>
                        
                        <Button 
                          type="text" 
                          danger 
                          size="small" 
                          className="override-builder-item__delete"
                          icon={<Trash2 size={13} />} 
                          onClick={() => handleRemoveOverride(item.name)} 
                        />
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="overrides-builder-empty">
                  No configuration overrides configured for this mode.
                </div>
              )}

              {availableFlags.length > 0 ? (
                <div className="overrides-add-control">
                  <Select
                    placeholder="Add configuration override..."
                    style={{ flex: 1 }}
                    value={selectedFlagToAdd}
                    onChange={(val) => handleAddOverride(val)}
                    showSearch
                    optionFilterProp="label"
                    options={availableFlags.map(f => ({
                      value: f.name,
                      label: f.name,
                      desc: f.description
                    }))}
                    optionRender={(option) => (
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 12, fontFamily: 'monospace' }}>{option.data.label}</div>
                        <div style={{ fontSize: 10, color: 'var(--color-text-tertiary, #bfbfbf)', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: 440 }}>{option.data.desc}</div>
                      </div>
                    )}
                  />
                </div>
              ) : (
                <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', textAlign: 'right', marginTop: 8 }}>All parameters overridden</div>
              )}
            </div>
          </div>
        </div>
      </Modal>
    </div>
  );
}
