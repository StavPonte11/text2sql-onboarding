import { Input, Segmented, Select, Tooltip } from 'antd';

import { useAgentModels } from '../../hooks/useEvaluations';

import type { EvalAgent } from '../../api/orchestration';

import './AgentModelSelect.css';

export const AGENT_LABELS: Record<EvalAgent, string> = {
  current: 'Current agent',
  previous: 'Previous agent (TextToSQL)',
};

const AGENT_OPTIONS: { label: string; value: EvalAgent }[] = [
  { label: 'Current', value: 'current' },
  { label: 'Previous (TextToSQL)', value: 'previous' },
];

/**
 * How an evaluation runs, laid out like the agent sandbox's header: the run's
 * name, then Agent | Model. `model` is '' for the agent's default model, which
 * the picker shows by name. The previous agent has no model choice.
 */
export function AgentModelSelect({
  value,
  onChange,
  agent = 'current',
  onAgentChange,
  runName,
  onRunNameChange,
  disabled = false,
}: {
  value: string;
  onChange: (model: string) => void;
  agent?: EvalAgent;
  onAgentChange?: (agent: EvalAgent) => void;
  runName?: string;
  onRunNameChange?: (name: string) => void;
  disabled?: boolean;
}) {
  const { data, isLoading } = useAgentModels();
  const defaultModel = data?.default ?? null;
  const models = [
    ...(defaultModel ? [defaultModel] : []),
    ...(data?.models ?? []).filter((model) => model !== defaultModel),
  ];
  const listError = data?.list_error ?? null;
  const isPrevious = agent === 'previous';

  const modelSelect = (
    <Select
      aria-label="LLM model for the evaluation"
      className="eval-run-settings__model"
      popupMatchSelectWidth={false}
      showSearch
      loading={isLoading}
      disabled={disabled || isPrevious}
      status={listError && !isPrevious ? 'warning' : undefined}
      // '' is the default model: show it by name
      value={isPrevious ? undefined : value || defaultModel || undefined}
      placeholder={isPrevious ? "The agent's own model" : "Agent's default model"}
      onChange={(model: string) => onChange(model === defaultModel ? '' : model)}
      options={models.map((model) => ({
        value: model,
        label:
          model === defaultModel ? (
            <span>
              {model} <span className="eval-run-settings__default">default</span>
            </span>
          ) : (
            model
          ),
      }))}
    />
  );

  return (
    <div className="eval-run-settings">
      {onRunNameChange && (
        <Input
          aria-label="Run name"
          placeholder="Run name (optional) — e.g. Baseline before prompt change"
          value={runName}
          onChange={(e) => onRunNameChange(e.target.value)}
          maxLength={200}
          allowClear
          disabled={disabled}
        />
      )}
      <div className="eval-run-settings__controls">
        {onAgentChange && (
          <div className="eval-run-settings__item">
            <span>Agent</span>
            <Segmented<EvalAgent>
              aria-label="Agent for the evaluation"
              value={agent}
              onChange={onAgentChange}
              disabled={disabled}
              options={AGENT_OPTIONS}
            />
          </div>
        )}
        <div className="eval-run-settings__item">
          <span>Model</span>
          {listError && !isPrevious ? (
            <Tooltip title={listError}>{modelSelect}</Tooltip>
          ) : (
            modelSelect
          )}
        </div>
      </div>
      {isPrevious && (
        <span className="eval-run-settings__note">
          The previous agent runs on its own model and looks in the evaluation service's
          PREVIOUS_AGENT_DATABASES (home_command by default), whatever the connection.
        </span>
      )}
    </div>
  );
}
