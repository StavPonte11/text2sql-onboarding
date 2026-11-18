import { Select } from 'antd';

/** Choose which question tags an evaluation runs; empty means all questions */
export function QuestionTagSelect({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string[];
  onChange: (tags: string[]) => void;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
        Run questions tagged
      </span>
      <Select
        mode="multiple"
        allowClear
        aria-label="Question tags to evaluate"
        style={{ flex: 1 }}
        placeholder={options.length ? 'All questions' : 'No tags on these questions'}
        disabled={!options.length}
        value={value}
        onChange={onChange}
        options={options}
      />
    </div>
  );
}
