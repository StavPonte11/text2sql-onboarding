import { useState } from 'react';
import { Button, DatePicker, Select, Tooltip } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';

/**
 * The time shift's days for a run. Each value is a number of days back ("-7")
 * or a date ("2023-10-07"): the data is shifted back to that date. The
 * evaluation service's five defaults are picked to start with.
 */
export const DEFAULT_TIME_SHIFT = ['-1', '-7', '-14', '-30', '-60'];

const DAYS_BACK = [1, 7, 14, 30, 60, 90, 180, 365];

/** Dates of major events, to shift the data back to */
export const TIME_SHIFT_EVENTS: { date: string; label: string }[] = [
  { date: '2023-10-07', label: '7 באוקטובר — תחילת חרבות ברזל' },
  { date: '2024-11-27', label: 'הפסקת האש בלבנון' },
  { date: '2025-01-19', label: 'הפסקת האש ועסקת החטופים' },
  { date: '2025-06-13', label: 'מבצע עם כלביא — תחילה' },
  { date: '2025-06-24', label: 'מבצע עם כלביא — סיום' },
  { date: '2025-10-10', label: 'הפסקת האש בעזה' },
  { date: '2026-02-28', label: 'מבצע שאגת הארי — תחילה' },
  { date: '2026-04-08', label: 'מבצע שאגת הארי — סיום (הפסקת אש)' },
];

const isDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value);

const formatDate = (value: string) => dayjs(value).format('DD.MM.YYYY');

/** A short label for one time shift value */
export function timeShiftLabel(value: string): string {
  if (isDate(value)) {
    const event = TIME_SHIFT_EVENTS.find((e) => e.date === value);
    return event ? `${formatDate(value)} · ${event.label}` : formatDate(value);
  }
  const days = Math.abs(Number(value));
  return days === 1 ? '1 day back' : `${days} days back`;
}

/** Same set as the defaults, in any order */
export const isDefaultTimeShift = (values: string[]) =>
  values.length === DEFAULT_TIME_SHIFT.length &&
  DEFAULT_TIME_SHIFT.every((value) => values.includes(value));

export function TimeShiftSelect({
  value,
  onChange,
  disabled = false,
}: {
  value: string[];
  onChange: (values: string[]) => void;
  disabled?: boolean;
}) {
  const [picking, setPicking] = useState<Dayjs | null>(null);
  const today = dayjs().startOf('day');
  const customDates = value.filter(
    (v) => isDate(v) && !TIME_SHIFT_EVENTS.some((event) => event.date === v),
  );

  const addDate = (day: Dayjs | null) => {
    setPicking(null);
    if (!day) return;
    const date = day.format('YYYY-MM-DD');
    if (!value.includes(date)) onChange([...value, date]);
  };

  return (
    <div className="eval-run-settings__time-shift">
      <Select
        mode="multiple"
        aria-label="Time shift days and dates"
        className="eval-run-settings__time-shift-select"
        value={value}
        onChange={(values: string[]) => onChange(values)}
        disabled={disabled}
        maxTagCount={6}
        placeholder="Pick days or dates to shift the data to"
        popupMatchSelectWidth={false}
        optionFilterProp="search"
        labelRender={({ value: v }) => timeShiftLabel(String(v))}
        options={[
          {
            label: 'Days back',
            options: DAYS_BACK.map((days) => ({
              value: String(-days),
              label: timeShiftLabel(String(-days)),
              search: `${days} days`,
            })),
          },
          {
            label: 'Events',
            options: TIME_SHIFT_EVENTS.map((event) => ({
              value: event.date,
              label: `${formatDate(event.date)} · ${event.label}`,
              search: `${event.date} ${formatDate(event.date)} ${event.label}`,
            })),
          },
          ...(customDates.length
            ? [
                {
                  label: 'Your dates',
                  options: customDates.map((date) => ({
                    value: date,
                    label: formatDate(date),
                    search: `${date} ${formatDate(date)}`,
                  })),
                },
              ]
            : []),
        ]}
      />
      <DatePicker
        aria-label="Add a date"
        placeholder="Add a date"
        format="DD.MM.YYYY"
        value={picking}
        onChange={addDate}
        disabled={disabled}
        // Shifting to today changes nothing
        disabledDate={(day) => day.isSame(today, 'day')}
      />
      {!isDefaultTimeShift(value) && (
        <Tooltip title="Back to 1, 7, 14, 30 and 60 days back">
          <Button size="small" type="link" onClick={() => onChange(DEFAULT_TIME_SHIFT)}>
            Defaults
          </Button>
        </Tooltip>
      )}
    </div>
  );
}
