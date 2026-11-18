import { useQuery } from '@tanstack/react-query';
import { Modal } from 'antd';
import { CheckCircle2, ExternalLink, MinusCircle, XCircle } from 'lucide-react';

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
        <pre className="question-sql__code">{sql}</pre>
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
  const scores = (trace?.scores ?? []).filter((s) => !s.name.endsWith('_seconds'));
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
          <p className="question-modal__question">{data.question}</p>
          {error && <pre className="question-error">{error}</pre>}
          {data.trace_unavailable_reason && (
            <p className="question-notice">{data.trace_unavailable_reason}</p>
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
