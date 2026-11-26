import { useEffect, useState } from 'react';
import { Hourglass, RotateCcw, Square } from 'lucide-react';

import { useRerunQuestions, useStopQuestionRetry } from '../../hooks/useEvaluations';

/** A question waiting out the LLM provider's rate limit (HTTP 429) */
export interface RateLimitedQuestion {
  question_id: string;
  question: string;
  /** Which LLM call was rate limited */
  stage: 'agent' | 'llm_judge' | 'order_judge' | string;
  retry: number;
  max_retries: number;
  /** When the next try starts (ISO) */
  retry_at: string;
  error?: string;
}

export const RATE_LIMITED = 'rate_limited';

export const isRateLimited = (failureType: string | null | undefined) =>
  !!failureType && failureType.startsWith(RATE_LIMITED);

const STAGE_LABEL: Record<string, string> = {
  agent: 'the agent writing the SQL',
  llm_judge: 'the LLM judge',
  order_judge: 'the order judge',
};

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

/** The questions of a running run that wait out a rate limit, each with a stop button */
export function RateLimitedQuestions({
  runId,
  waiting,
}: {
  runId: string;
  waiting: RateLimitedQuestion[];
}) {
  const now = useNow();
  const stopMut = useStopQuestionRetry();
  if (!waiting.length) return null;

  return (
    <div className="card rate-limit-card" role="status" aria-live="polite">
      <div className="rate-limit-card__title">
        <Hourglass size={14} />
        Rate limited by the LLM provider — retrying {waiting.length}{' '}
        {waiting.length === 1 ? 'question' : 'questions'}
      </div>
      {waiting.map((w) => {
        const seconds = Math.max(0, Math.round((Date.parse(w.retry_at) - now) / 1000));
        const stopping = stopMut.isPending && stopMut.variables?.questionId === w.question_id;
        return (
          <div key={w.question_id} className="rate-limit-item">
            <div className="rate-limit-item__text">
              <div className="rate-limit-item__question" title={w.question}>
                {w.question}
              </div>
              <div className="rate-limit-item__detail" title={w.error}>
                {STAGE_LABEL[w.stage] ?? w.stage} got HTTP 429 · retry {w.retry} of {w.max_retries}{' '}
                {seconds > 0 ? `in ${seconds}s` : 'now'}
              </div>
            </div>
            <button
              className="btn btn--ghost btn--sm rate-limit-item__stop"
              disabled={stopping}
              onClick={() => stopMut.mutate({ runId, questionId: w.question_id })}
              title="Stop retrying: the question is marked rate limited, and you can rerun it later"
            >
              <Square size={11} fill="currentColor" />
              {stopping ? 'Stopping…' : 'Stop retrying'}
            </button>
          </div>
        );
      })}
      {stopMut.isError && (
        <div className="rate-limit-card__error">
          {(stopMut.error as any)?.response?.data?.detail ?? 'Could not stop the retries'}
        </div>
      )}
    </div>
  );
}

/** Rerun every question of a finished run that the rate limit stopped */
export function RerunRateLimitedButton({ runId, count }: { runId: string; count: number }) {
  const rerunMut = useRerunQuestions();
  if (count <= 0) return null;
  return (
    <button
      className="btn btn--secondary btn--sm rate-limit-rerun"
      disabled={rerunMut.isPending}
      onClick={() => rerunMut.mutate({ runId })}
      title="Evaluate the questions the LLM rate limit stopped again, with this run's settings"
    >
      <RotateCcw size={12} />
      {rerunMut.isPending ? 'Starting…' : `Rerun rate-limited (${count})`}
    </button>
  );
}

/** Rerun one question of a finished run */
export function RerunQuestionButton({ runId, questionId }: { runId: string; questionId: string }) {
  const rerunMut = useRerunQuestions();
  return (
    <button
      className="btn btn--ghost btn--sm rate-limit-rerun rate-limit-rerun--inline"
      disabled={rerunMut.isPending}
      onClick={(e) => {
        e.stopPropagation();
        rerunMut.mutate({ runId, questionIds: [questionId] });
      }}
      onKeyDown={(e) => e.stopPropagation()}
      title="Evaluate this question again, with this run's settings"
    >
      <RotateCcw size={11} />
      {rerunMut.isPending ? '…' : 'Rerun'}
    </button>
  );
}
