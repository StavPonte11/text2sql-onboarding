import { Select } from 'antd';

import { useAgentModels } from '../../hooks/useEvaluations';

/** Choose the LLM model the agent answers with; empty means the agent's default */
export function AgentModelSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (model: string) => void;
}) {
  const { data, isLoading } = useAgentModels();
  const models = data?.models ?? [];
  const defaultLabel = data?.default ? `Default (${data.default})` : 'Default model';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
          LLM model
        </span>
        <Select
          aria-label="LLM model for the evaluation"
          style={{ flex: 1 }}
          loading={isLoading}
          value={value}
          onChange={onChange}
          showSearch
          options={[
            { value: '', label: defaultLabel },
            ...models
              .filter((model) => model !== data?.default)
              .map((model) => ({ value: model, label: model })),
          ]}
        />
      </div>
      {data?.list_error && (
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{data.list_error}</span>
      )}
    </div>
  );
}
