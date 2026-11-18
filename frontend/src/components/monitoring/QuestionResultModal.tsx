import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Modal } from 'antd';
import { Check, CheckCircle2, Copy, ExternalLink, Gavel, MinusCircle, XCircle } from 'lucide-react';

import { orchestrationApi } from '../../api/orchestration';
import { ScoreBar, Spinner } from '../common/EvalUI';

import type { QuestionScore, QuestionSqlRun } from '../../api/orchestration';

import './QuestionResultModal.css';

const label = (name: string) => name.replace(/_/g, ' ');

function formatMs(ms: unknown): string | null {
  if (typeof ms !== 'number') return null;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

function formatCell(cell: unknown): string {
  if (cell === null || cell === undefined) return 'NULL';
  return typeof cell === 'object' ? JSON.stringify(cell) : String(cell);
}

function formatValue(value: unknown): string {
  return typeof value === 'object' && value !== null
    ? JSON.stringify(value, null, 2)
    : String(value);
}

/** A result as tab-separated text with a header row, which pastes into a spreadsheet */
function resultAsTsv(columns: string[], rows: unknown[][]): string {
  const cell = (value: unknown) => formatCell(value).replace(/[\t\n\r]+/g, ' ');
  return [columns.map(cell), ...rows.map((row) => row.map(cell))]
    .map((line) => line.join('\t'))
    .join('\n');
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <button type="button" className="question-copy" onClick={copy} aria-label={label} title={label}>
      {copied ? <Check size={12} /> : <Copy size={12} />}
      {copied ? 'Copied' : label}
    </button>
  );
}

function ScoreRow({ score }: { score: QuestionScore }) {
  const { value, passed, applicable, error, details } = score;
  const isFraction = typeof value === 'number' && value >= 0 && value <= 1;
  const reason = error || (typeof details.reason === 'string' ? details.reason : null);
  const hasDetails = Object.keys(details).length > 0;
  const color = !applicable
    ? 'var(--text-muted)'
    : passed === null
      ? 'var(--text-secondary)'
      : passed
        ? '#10b981'
        : '#ef4444';

  return (
    <div className="question-score">
      <div className="question-score__head">
        {!applicable ? (
          <MinusCircle size={14} style={{ color: 'var(--text-muted)' }} />
        ) : passed === null ? null : passed ? (
          <CheckCircle2 size={14} style={{ color }} />
        ) : (
          <XCircle size={14} style={{ color }} />
        )}
        <span className="question-score__name">{label(score.name)}</span>
        <span className="question-score__value" style={{ color }}>
          {!applicable
            ? 'N/A'
            : isFraction
              ? `${Math.round(value * 100)}%`
              : (value?.toLocaleString() ?? '—')}
        </span>
      </div>
      {applicable && isFraction && <ScoreBar score={value} height={4} />}
      {reason && <div className="question-score__reason">{label(reason)}</div>}
      {hasDetails && (
        <details className="question-score__details">
          <summary>Details</summary>
          <pre>{JSON.stringify(details, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

function SqlRunPanel({
  title,
  run,
  fallbackSql,
  note,
}: {
  title: string;
  run: QuestionSqlRun | null;
  fallbackSql: string | null;
  note?: string;
}) {
  const columns = run?.columns ?? [];
  const rows = run?.rows ?? [];
  const sql = run?.sql || fallbackSql;
  const executionTime = formatMs(run?.execution_time_ms);

  return (
    <section className="question-sql">
      <div className="question-sql__head">
        <span className="question-sql__title">{title}</span>
        {run && (
          <span className={`question-pill question-pill--${run.success ? 'pass' : 'fail'}`}>
            {run.success ? 'Executed' : 'Failed'}
          </span>
        )}
      </div>
      {sql ? (
        <div className="question-copyable">
          <pre className="question-sql__code">{sql}</pre>
          <CopyButton text={sql} label={`Copy ${title}`} />
        </div>
      ) : (
        <p className="question-muted">No SQL</p>
      )}
      {note && <p className="question-muted">{note}</p>}

      {!run ? (
        <p className="question-muted">Not executed</p>
      ) : (
        <>
          <div className="question-sql__meta">
            <span>
              {(run.total_rows ?? run.row_count ?? 0).toLocaleString()} row
              {(run.total_rows ?? run.row_count) === 1 ? '' : 's'}
            </span>
            {executionTime && <span>{executionTime}</span>}
            {run.truncated && <span>showing first {rows.length}</span>}
            {run.truncated_by_executor && <span>cut off by the row limit</span>}
          </div>
          {run.error && <pre className="question-error">{run.error}</pre>}
          {columns.length > 0 && (
            <CopyButton
              text={resultAsTsv(columns, rows)}
              label={`Copy ${title.replace(/ SQL$/, '')} result`}
            />
          )}
          {columns.length > 0 && (
            <div className="question-table-wrap">
              <table className="question-table">
                <thead>
                  <tr>
                    {columns.map((column, i) => (
                      <th key={i}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, r) => (
                    <tr key={r}>
                      {row.map((cell, c) => (
                        <td key={c}>{formatCell(cell)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

const JUDGE_NOT_USED: Record<string, string> = {
  generated_sql_did_not_run:
    "Not used: the generated SQL didn't run, so there was no result to judge.",
  judge_unavailable: 'The judge was asked but could not answer',
};

/** Whether the LLM judge took a second look at this question, and what it decided */
function JudgeSection({
  judge,
  status,
}: {
  judge: QuestionScore | undefined;
  status: string | null;
}) {
  if (!judge) {
    return (
      <p className="question-muted">
        {status === 'pass'
          ? 'Contains accuracy: not needed, the question passed it.'
          : 'Contains accuracy: not used, the LLM judge was not enabled for this run.'}
      </p>
    );
  }
  const d = judge.details as Record<string, any>;
  if (!judge.applicable) {
    const reason = String(d.reason ?? '');
    return (
      <p className="question-muted">
        {JUDGE_NOT_USED[reason] ?? label(reason)}
        {d.error ? `: ${d.error}` : ''}
      </p>
    );
  }
  const overturned = Boolean(d.overturned);
  const differences: Record<string, any>[] = Array.isArray(d.key_differences)
    ? d.key_differences
    : [];
  return (
    <div className={`question-judge question-judge--${overturned ? 'overturned' : 'upheld'}`}>
      <div className="question-judge__verdict">
        <Gavel size={14} />
        <strong>
          {overturned
            ? 'Overturned: judged correct, so the question passes'
            : d.is_false_negative
              ? 'Leaned correct, but not confident enough to overturn'
              : 'Upheld: the answer is wrong'}
        </strong>
        {d.confidence != null && (
          <span className="question-judge__confidence">
            confidence {d.confidence}% (needs {d.min_confidence ?? 80}%)
          </span>
        )}
      </div>
      {d.reasoning && <p className="question-judge__reasoning">{d.reasoning}</p>}
      {differences.length > 0 && (
        <ul className="question-judge__differences">
          {differences.map((diff, i) => (
            <li key={i}>
              <span>{String(diff.difference ?? '')}</span>
              {String(diff.prevents_correct_answer) === 'true' ? (
                <em> (prevents a correct answer)</em>
              ) : (
                <em> (cosmetic)</em>
              )}
              {diff.explanation && <div className="question-muted">{String(diff.explanation)}</div>}
            </li>
          ))}
        </ul>
      )}
      <div className="question-muted">
        Metrics failed it for: {label(String(d.judged_failure_reason ?? 'results differ'))}
        {d.model ? ` · model ${d.model}` : ''}
        {d.prompt_name
          ? ` · prompt ${d.prompt_name}${d.prompt_version ? ` v${d.prompt_version}` : ''}`
          : ''}
      </div>
    </div>
  );
}

/** The judge's verdict on each time shift the contains check failed */
function ShiftJudgeSection({ timeShift }: { timeShift: QuestionScore | undefined }) {
  const shifts = (timeShift?.details?.shift_results ?? []) as Record<string, any>[];
  const judged = shifts.filter((shift) => shift.llm_judge);
  if (!timeShift?.applicable || !shifts.length) return null;
  if (!judged.length) {
    return (
      <p className="question-muted">
        Time shifts: none needed the judge (no shift failed the contains check).
      </p>
    );
  }
  const passed = judged.filter((shift) => shift.llm_judge.overturned).length;
  return (
    <div className="question-judge-shifts">
      <div className="question-judge__verdict">
        <Gavel size={14} />
        <strong>
          Time shifts: judged {judged.length} failed shift{judged.length === 1 ? '' : 's'}, passed{' '}
          {passed}
        </strong>
      </div>
      <ul className="question-judge__differences">
        {judged.map((shift) => {
          const verdict = shift.llm_judge;
          return (
            <li key={shift.offset_days}>
              <strong>{shift.offset_days} days:</strong>{' '}
              {verdict.error
                ? `judge unavailable (${verdict.error})`
                : verdict.overturned
                  ? 'overturned, judged correct'
                  : verdict.is_false_negative
                    ? 'leaned correct, not confident enough'
                    : 'upheld, wrong'}
              {verdict.confidence != null && (
                <span className="question-muted"> · confidence {verdict.confidence}%</span>
              )}
              {verdict.reasoning && <div className="question-muted">{verdict.reasoning}</div>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const SCORE_SOURCE: Record<string, string> = {
  time_shift: 'its time shift score',
  contains_accuracy: 'its contains accuracy (no time shift for this question)',
};

// Shown as tiles above the scores; the rest of the metadata goes under "Run information"
const TILE_KEYS = [
  'total_latency_ms',
  'agent_latency_ms',
  'agent_sql_generation_seconds',
  'total_execution_time_ms',
  'expected_row_count',
  'generated_row_count',
  'token_usage',
  'refiner_iteration_count',
  'error',
];

export function QuestionResultModal({
  runId,
  questionId,
  onClose,
}: {
  runId: string;
  questionId: string;
  onClose: () => void;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['run-question', runId, questionId],
    queryFn: () => orchestrationApi.getRunQuestion(runId, questionId),
  });

  const trace = data?.trace;
  const metadata = trace?.metadata ?? {};
  const allScores = trace?.scores ?? [];
  const judge = allScores.find((s) => s.name === 'llm_judge');
  const scores = allScores.filter((s) => !s.name.endsWith('_seconds') && s.name !== 'llm_judge');
  const error = trace?.error || data?.failure_type;

  const tiles = [
    { label: 'Total time', value: formatMs(metadata.total_latency_ms) },
    { label: 'Agent SQL generation', value: formatMs(metadata.agent_latency_ms) },
    { label: 'Generated SQL run', value: formatMs(trace?.generated_run?.execution_time_ms) },
    { label: 'Golden SQL run', value: formatMs(trace?.expected_run?.execution_time_ms) },
    { label: 'Golden rows', value: metadata.expected_row_count },
    { label: 'Generated rows', value: metadata.generated_row_count },
    { label: 'Tokens', value: metadata.token_usage },
    { label: 'Refiner iterations', value: metadata.refiner_iteration_count },
    { label: 'Agent status', value: trace?.agent_status },
  ].filter((tile) => tile.value !== null && tile.value !== undefined);

  const otherMetadata = Object.entries(metadata).filter(
    ([key, value]) => !TILE_KEYS.includes(key) && value !== null && value !== undefined,
  );

  return (
    <Modal
      open
      onCancel={onClose}
      footer={null}
      width={1040}
      className="question-modal"
      title={
        <div className="question-modal__title">
          <span>Question Result</span>
          {data?.status && (
            <span className={`question-pill question-pill--${data.status}`}>
              {data.status === 'pass' ? 'Passed' : 'Failed'}
            </span>
          )}
          {trace?.langfuse_url && (
            <a href={trace.langfuse_url} target="_blank" rel="noreferrer">
              <ExternalLink size={12} /> Open in Langfuse
            </a>
          )}
        </div>
      }
    >
      {isLoading && (
        <div className="question-modal__loading">
          <Spinner size={26} />
        </div>
      )}
      {isError && <pre className="question-error">Could not load this question's result.</pre>}

      {data && (
        <div className="question-modal__body">
          <div className="question-modal__question-row">
            <p className="question-modal__question">{data.question}</p>
            <CopyButton text={data.question} label="Copy question" />
          </div>
          {error && <pre className="question-error">{error}</pre>}
          {data.trace_unavailable_reason && (
            <p className="question-notice">{data.trace_unavailable_reason}</p>
          )}

          {data.final_score !== undefined && (
            <p className="question-final-score">
              Score{' '}
              <strong>
                {data.final_score == null ? 'N/A' : `${Math.round(data.final_score * 100)}%`}
              </strong>
              {data.score_source && (
                <span className="question-muted"> = {SCORE_SOURCE[data.score_source]}</span>
              )}
            </p>
          )}

          {tiles.length > 0 && (
            <div className="question-tiles">
              {tiles.map((tile) => (
                <div key={tile.label} className="question-tile">
                  <div className="question-tile__label">{tile.label}</div>
                  <div className="question-tile__value">{formatValue(tile.value)}</div>
                </div>
              ))}
            </div>
          )}

          {scores.length > 0 && (
            <>
              <h3 className="question-heading">Scores</h3>
              <div className="question-scores">
                {scores.map((score) => (
                  <ScoreRow key={score.name} score={score} />
                ))}
              </div>
            </>
          )}

          {trace && (
            <>
              <h3 className="question-heading">LLM Judge</h3>
              <JudgeSection judge={judge} status={data.status} />
              <ShiftJudgeSection timeShift={allScores.find((s) => s.name === 'time_shift')} />
            </>
          )}

          <h3 className="question-heading">Trino Runs</h3>
          <div className="question-sql-grid">
            <SqlRunPanel
              title="Golden SQL"
              run={trace?.expected_run ?? null}
              fallbackSql={trace?.expected_sql ?? data.expected_sql}
            />
            <SqlRunPanel
              title="Generated SQL"
              run={trace?.generated_run ?? null}
              fallbackSql={trace?.generated_sql ?? null}
              note={
                trace?.executed_generated_sql
                  ? 'Transpiled to the connection dialect before it ran.'
                  : undefined
              }
            />
          </div>

          {otherMetadata.length > 0 && (
            <>
              <h3 className="question-heading">Run Information</h3>
              <dl className="question-info">
                {otherMetadata.map(([key, value]) => (
                  <div key={key}>
                    <dt>{label(key)}</dt>
                    <dd>{formatValue(value)}</dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
