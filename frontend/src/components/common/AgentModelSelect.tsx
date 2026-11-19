import { Select } from 'antd';

import { useAgentModels } from '../../hooks/useEvaluations';

import type { EvalAgent } from '../../api/orchestration';

export const AGENT_LABELS: Record<EvalAgent, string> = {
  current: 'Current agent',
  previous: 'Previous agent (TextToSQL)',
};

const labelStyle = { fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' } as const;
const rowStyle = { display: 'flex', alignItems: 'center', gap: 10 } as const;

/**
 * Choose the Text-to-SQL agent and, for the current agent, the LLM model it
 * answers with (empty means its default). The previous agent has no model choice.
 */
export function AgentModelSelect({
  value,
  onChange,
  agent = 'current',
  onAgentChange,
}: {
  value: string;
  onChange: (model: string) => void;
  agent?: EvalAgent;
  onAgentChange?: (agent: EvalAgent) => void;
}) {
  const { data, isLoading } = useAgentModels();
  const models = data?.models ?? [];
  const defaultLabel = data?.default ? `Default (${data.default})` : 'Default model';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {onAgentChange && (
        <div style={rowStyle}>
          <span style={labelStyle}>Agent</span>
          <Select
            aria-label="Agent for the evaluation"
            style={{ flex: 1 }}
            value={agent}
            onChange={onAgentChange}
            options={(Object.keys(AGENT_LABELS) as EvalAgent[]).map((name) => ({
              value: name,
              label: AGENT_LABELS[name],
            }))}
          />
        </div>
      )}
      {agent === 'current' ? (
        <>
          <div style={rowStyle}>
            <span style={labelStyle}>LLM model</span>
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
        </>
      ) : (
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
          The previous agent runs on its own model and looks in the evaluation service's
          PREVIOUS_AGENT_DATABASES (home_command by default), whatever the connection.
        </span>
      )}
    </div>
  );
}
