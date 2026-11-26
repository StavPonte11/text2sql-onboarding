import { useEffect } from 'react';
import { Input, Segmented, Select, Tooltip } from 'antd';

import { useAgentModels } from '../../hooks/useEvaluations';
import { TimeShiftSelect } from './TimeShiftSelect';

import type { EvalAgent } from '../../api/orchestration';

import './AgentModelSelect.css';

export const AGENT_LABELS: Record<EvalAgent, string> = {
  current: 'Structured agent',
  deep: 'Agentic agent',
  previous: 'Legacy agent',
};

const AGENT_OPTIONS: { label: string; value: EvalAgent }[] = [
  { label: 'Structured', value: 'current' },
  { label: 'Agentic', value: 'deep' },
  { label: 'Legacy', value: 'previous' },
];

/**
 * How an evaluation runs, laid out like the agent sandbox's header: the run's
 * name, then Agent | Model. `model` is '' for the agent's default model, which
 * the picker shows by name. The Legacy agent has no model choice.
 */
export function AgentModelSelect({
  value,
  onChange,
  agent = 'current',
  onAgentChange,
  runName,
  onRunNameChange,
  timeShift,
  onTimeShiftChange,
  disabled = false,
}: {
  value: string;
  onChange: (model: string) => void;
  agent?: EvalAgent;
  onAgentChange?: (agent: EvalAgent) => void;
  runName?: string;
  onRunNameChange?: (name: string) => void;
  /** The time shift's days/dates; shown when onTimeShiftChange is given */
  timeShift?: string[];
  onTimeShiftChange?: (values: string[]) => void;
  disabled?: boolean;
}) {
  const { data, isLoading } = useAgentModels();
  const defaultModel = data?.default ?? null;
  const models = [
    ...(defaultModel ? [defaultModel] : []),
    ...(data?.models ?? []).filter((model) => model !== defaultModel),
  ];
  const listError = data?.list_error ?? null;
  const agents = data?.agents;
  // Before the agent server answers, only the structured agent is offered
  const agentInfo = (name: EvalAgent) =>
    agents?.[name] ?? { available: name !== 'deep', supports_model: name === 'current' };
  // The previous agent, and agent versions whose tool takes no model, run on their own
  const isPrevious = agent === 'previous';
  const noModelChoice = isPrevious || (!!agents && !agentInfo(agent).supports_model);

  // A model picked for another agent is not sent to one that can't take it
  useEffect(() => {
    if (noModelChoice && value) onChange('');
  }, [noModelChoice, value, onChange]);

  const modelSelect = (
    <Select
      aria-label="LLM model for the evaluation"
      className="eval-run-settings__model"
      popupMatchSelectWidth={false}
      showSearch
      loading={isLoading}
      disabled={disabled || noModelChoice}
      status={listError && !isPrevious ? 'warning' : undefined}
      // '' is the default model: show it by name
      value={isPrevious ? undefined : value || defaultModel || undefined}
      placeholder={isPrevious ? "The agent's own model" : "Agent's default model"}
      onChange={(model: string) => onChange(model === defaultModel ? '' : model)}
      // the box shows the name alone; "default" is marked in the list
      labelRender={({ value: model }) => model}
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
              options={AGENT_OPTIONS.map((option) => {
                const info = agentInfo(option.value);
                return {
                  value: option.value,
                  disabled: !info.available,
                  label:
                    info.available || !info.reason ? (
                      option.label
                    ) : (
                      <Tooltip title={info.reason}>
                        <span>{option.label}</span>
                      </Tooltip>
                    ),
                };
              })}
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
      {onTimeShiftChange && (
        <div className="eval-run-settings__item eval-run-settings__item--wide">
          <span>Time shift</span>
          <TimeShiftSelect
            value={timeShift ?? []}
            onChange={onTimeShiftChange}
            disabled={disabled}
          />
        </div>
      )}
      {onTimeShiftChange && timeShift?.length === 0 && (
        <span className="eval-run-settings__note">
          Nothing picked: the run uses the defaults (1, 7, 14, 30 and 60 days back).
        </span>
      )}
      {agent === 'deep' && noModelChoice && (
        <span className="eval-run-settings__note">
          This Agentic agent version can't switch models; it runs on its own.
        </span>
      )}
      {isPrevious && (
        <span className="eval-run-settings__note">
          The Legacy agent runs on its own model and looks in the evaluation service's
          PREVIOUS_AGENT_DATABASES (home_command by default), whatever the connection.
        </span>
      )}
    </div>
  );
}
