import { Tooltip } from 'antd';
import { Info } from 'lucide-react';

import type { ScoreBreakdown } from '../../api/orchestration';

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;
const avg = (value: number | null) => (value == null ? '—' : value.toFixed(2));

/** How a run's score was calculated, line by line */
export function scoreExplanation(breakdown: ScoreBreakdown, score: number | null): string[] {
  const { time_shift: shift, contains_accuracy: contains, questions, unscored } = breakdown;
  const lines = [
    "Each question's score is its time shift score; questions without a time shift use their contains accuracy.",
  ];
  if (shift.count) {
    const pct = Math.round((shift.count / questions) * 100);
    lines.push(
      `${plural(shift.count, 'question')} (${pct}%) scored by time shift: average ${avg(shift.average)}`,
    );
  }
  if (contains.count) {
    const pct = Math.round((contains.count / questions) * 100);
    lines.push(
      `${plural(contains.count, 'question')} (${pct}%) scored by contains accuracy: average ${avg(contains.average)}`,
    );
  }
  if (questions && score != null) {
    const parts = [shift, contains]
      .filter((p) => p.count)
      .map((p) => `${p.count} × ${avg(p.average)}`);
    lines.push(`Score = (${parts.join(' + ')}) / ${questions} = ${score.toFixed(2)}`);
  }
  if (unscored) lines.push(`${plural(unscored, 'question')} could not be scored and are left out.`);
  if (!questions) lines.push('No question has been scored yet.');
  return lines;
}

/** An (i) icon; hovering it shows how the score was calculated */
export function ScoreInfo({
  breakdown,
  score,
}: {
  breakdown: ScoreBreakdown | null | undefined;
  score: number | null | undefined;
}) {
  if (!breakdown) return null;
  return (
    <Tooltip
      title={
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
          {scoreExplanation(breakdown, score ?? null).map((line) => (
            <div key={line}>{line}</div>
          ))}
        </div>
      }
      overlayStyle={{ maxWidth: 380 }}
    >
      <span
        role="img"
        aria-label="How the score is calculated"
        tabIndex={0}
        onClick={(e) => e.stopPropagation()}
        style={{ display: 'inline-flex', cursor: 'help', color: 'var(--text-muted)' }}
      >
        <Info size={13} />
      </span>
    </Tooltip>
  );
}
