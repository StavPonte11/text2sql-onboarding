import { useEffect, useState } from 'react';
import { useFieldArray, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App } from 'antd';
import { Check, CheckCircle2, CloudDownload } from 'lucide-react';
import { z } from 'zod';

import { enrichmentApi, questionsApi, tablesApi } from '../../api/client';

import type { EnrichmentData, GoldenQuestionCreate, Table } from '../../types';

const STEPS = ['select', 'schema', 'enrichment', 'validate', 'questions', 'submit'] as const;

const columnSchema = z.object({
  name: z.string().trim().min(1, 'Column name is required'),
  description: z.string().trim().min(20, 'Description must be at least 20 characters'),
});

const questionSchema = z.object({
  question: z.string().trim().min(1, 'Question is required'),
  expected_sql: z.string().trim().min(1, 'Expected SQL is required'),
  difficulty: z.enum(['simple', 'medium', 'complex']),
  question_type: z.enum(['join', 'simple', 'complex', 'geo', 'aggregate', 'time_series']),
});

const wizardSchema = z.object({
  oasis_source_id: z.string().trim().min(1, 'Oasis Source ID is required'),
  table_description: z.string().trim().min(20, 'Table description must be at least 20 characters'),
  columns: z.array(columnSchema),
  questions: z.array(questionSchema),
});

type WizardFormValues = z.infer<typeof wizardSchema>;

export function OnboardingWizard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { message } = App.useApp();

  const [currentStep, setCurrentStep] = useState(0);
  const [done, setDone] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [createdTableId, setCreatedTableId] = useState<string | null>(null);
  const [createdTable, setCreatedTable] = useState<Table | null>(null);
  const [isFetchingSchema, setIsFetchingSchema] = useState(false);

  const {
    register,
    control,
    trigger,
    getValues,
    watch,
    formState: { errors },
  } = useForm<WizardFormValues>({
    resolver: zodResolver(wizardSchema),
    defaultValues: {
      oasis_source_id: '',
      table_description: '',
      columns: [],
      questions: [],
    },
    mode: 'onTouched',
  });

  const {
    fields: columnFields,
    replace: replaceColumns,
    append: appendColumn,
    remove: removeColumn,
  } = useFieldArray({
    control,
    name: 'columns',
  });

  const {
    fields: questionFields,
    append: appendQuestion,
    remove: removeQuestion,
  } = useFieldArray({
    control,
    name: 'questions',
  });

  const createTableMutation = useMutation({ mutationFn: tablesApi.create });
  const createEnrichmentMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: EnrichmentData }) =>
      enrichmentApi.create(id, data),
  });
  const createQuestionMutation = useMutation({
    mutationFn: ({ id, q }: { id: string; q: GoldenQuestionCreate }) => questionsApi.create(id, q),
  });

  const step = STEPS[currentStep];

  const watchOasisSourceId = watch('oasis_source_id');
  const watchTableDescription = watch('table_description');
  const watchColumns = watch('columns') || [];
  const watchQuestions = watch('questions') || [];

  useEffect(() => {
    setSubmitError(null);
  }, [watchOasisSourceId, watchTableDescription, watchColumns.length, watchQuestions.length]);

  const handleFetchSchema = () => {
    setIsFetchingSchema(true);
    setTimeout(() => {
      replaceColumns([
        { name: 'id', description: '' },
        { name: 'created_at', description: '' },
        { name: 'status', description: '' },
      ]);
      setIsFetchingSchema(false);
      message.success(`Fetched 3 columns from OpenMetadata`);
    }, 1500);
  };

  const handleNext = async () => {
    setSubmitError(null);

    let fieldsToValidate: any[] = [];
    if (step === 'select') fieldsToValidate = ['oasis_source_id'];
    if (step === 'enrichment') fieldsToValidate = ['table_description', 'columns'];
    if (step === 'validate') fieldsToValidate = ['table_description', 'columns'];
    if (step === 'questions') fieldsToValidate = ['questions'];

    if (fieldsToValidate.length > 0) {
      const isValid = await trigger(fieldsToValidate as any);
      if (!isValid) return;
    }

    try {
      if (step === 'select') {
        const t = await createTableMutation.mutateAsync({
          oasis_source_id: getValues('oasis_source_id'),
        });
        setCreatedTableId(t.id);
        setCreatedTable(t);
      }
      if (step === 'enrichment' && createdTableId) {
        await createEnrichmentMutation.mutateAsync({
          id: createdTableId,
          data: {
            table_description: getValues('table_description'),
            columns: getValues('columns'),
          },
        });
        message.success('Enrichment saved successfully');
      }
      if (step === 'questions' && createdTableId) {
        const qs = getValues('questions') || [];
        await Promise.all(
          qs.map((q) => createQuestionMutation.mutateAsync({ id: createdTableId, q })),
        );
        message.success('Golden questions added');
        qc.invalidateQueries({ queryKey: ['tables'] });
        setDone(true);
        return;
      }
      setCurrentStep((s) => Math.min(s + 1, STEPS.length - 1));
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || 'An unexpected error occurred';
      setSubmitError(msg);
      message.error(msg);
    }
  };

  const addQuestion = () =>
    appendQuestion({
      question: '',
      expected_sql: '',
      difficulty: 'simple',
      question_type: 'simple',
    });

  if (done) {
    return (
      <div className="page">
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '80px 0',
            gap: 16,
          }}
        >
          <CheckCircle2 size={64} color="var(--status-production)" />
          <h2 style={{ fontSize: 22, fontWeight: 700 }}>{t('wizard.finish')}</h2>
          <p style={{ color: 'var(--text-muted)' }}>Table ID: {createdTableId}</p>
          <div className="flex gap-2">
            <button className="btn btn--ghost" onClick={() => navigate('/tables')}>
              View All Tables
            </button>
            <button
              className="btn btn--primary"
              onClick={() => navigate(`/tables/${createdTableId}`)}
            >
              Go to Table
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">{t('wizard.title')}</h1>
          <p className="page__subtitle">
            Step {currentStep + 1} of {STEPS.length}
          </p>
        </div>
      </div>

      {/* Stepper */}
      <div className="stepper" style={{ marginBottom: 32 }}>
        {STEPS.map((s, i) => (
          <div key={s} className="stepper__step">
            <div
              className={`stepper__circle${i < currentStep ? ' stepper__circle--done' : i === currentStep ? ' stepper__circle--active' : ''}`}
            >
              {i < currentStep ? <Check size={14} /> : i + 1}
            </div>
            <div className={`stepper__label${i === currentStep ? ' stepper__label--active' : ''}`}>
              {t(`wizard.steps.${s}`)}
            </div>
            {i < STEPS.length - 1 && (
              <div className={`stepper__line${i < currentStep ? ' stepper__line--done' : ''}`} />
            )}
          </div>
        ))}
      </div>

      {/* Step content */}
      <div className="card" style={{ marginBottom: 24 }}>
        {submitError && (
          <div
            style={{
              padding: '10px 12px',
              borderRadius: 'var(--radius-sm)',
              background: 'rgba(239, 68, 68, 0.08)',
              color: 'var(--status-degraded)',
              border: '1px solid rgba(239, 68, 68, 0.2)',
              fontSize: '13px',
              marginBottom: '16px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            {submitError}
          </div>
        )}

        {step === 'select' && (
          <div>
            <h3 style={{ fontWeight: 700, marginBottom: 16 }}>{t('wizard.steps.select')}</h3>
            <div className="form-group">
              <label className="form-label">Oasis Source ID</label>
              <input
                className={`form-input${errors.oasis_source_id ? ' form-input--error' : ''}`}
                placeholder="e.g. some-uuid-or-fqn"
                {...register('oasis_source_id')}
              />
              {errors.oasis_source_id && (
                <div className="form-error">{errors.oasis_source_id.message}</div>
              )}
            </div>
          </div>
        )}

        {step === 'schema' && (
          <div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 16,
              }}
            >
              <h3 style={{ fontWeight: 700 }}>{t('wizard.steps.schema')}</h3>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={handleFetchSchema}
                disabled={isFetchingSchema}
              >
                <CloudDownload size={14} />
                {isFetchingSchema ? 'Fetching...' : 'Fetch Schema'}
              </button>
            </div>
            <div style={{ background: 'var(--bg-base)', borderRadius: 8, padding: 16 }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>
                Table: <code>{createdTable?.name || watchOasisSourceId}</code>
              </div>
              <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>
                Schema: <code>{createdTable?.schema_name || ''}</code>
              </div>

              {watchColumns.length > 0 ? (
                <div
                  style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>
                    Discovered Columns:
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {watchColumns.map((col, i) => (
                      <code
                        key={i}
                        style={{
                          padding: '4px 8px',
                          background: 'var(--bg-hover)',
                          borderRadius: 4,
                          fontSize: 12,
                        }}
                      >
                        {col.name}
                      </code>
                    ))}
                  </div>
                </div>
              ) : (
                <div style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 4 }}>
                  The actual schema will be fetched from your OpenMetadata catalog in production.
                  Click "Fetch Schema" to simulate.
                </div>
              )}
            </div>
          </div>
        )}

        {(step === 'enrichment' || step === 'validate') && (
          <div>
            <h3 style={{ fontWeight: 700, marginBottom: 16 }}>
              {step === 'validate' ? t('wizard.steps.validate') : t('wizard.steps.enrichment')}
            </h3>
            <div className="form-group">
              <label className="form-label">Table Description</label>
              <textarea
                className={`form-textarea${errors.table_description ? ' form-textarea--error' : ''}`}
                rows={3}
                placeholder="Describe this table in at least 20 characters..."
                {...register('table_description')}
              />
              {errors.table_description && (
                <div className="form-error">{errors.table_description.message}</div>
              )}
              <div className="text-sm text-muted" style={{ marginTop: 4 }}>
                {watchTableDescription.length} / 20 chars minimum
              </div>
            </div>
            <div style={{ marginBottom: 10, display: 'flex', justifyContent: 'space-between' }}>
              <label className="form-label" style={{ marginBottom: 0 }}>
                Columns
              </label>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => appendColumn({ name: '', description: '' })}
              >
                + Add Column
              </button>
            </div>
            {columnFields.map((field, i) => {
              const nameError = errors.columns?.[i]?.name;
              const descError = errors.columns?.[i]?.description;

              return (
                <div key={field.id} style={{ marginBottom: 12 }}>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '1fr 1fr auto',
                      gap: 8,
                    }}
                  >
                    <input
                      className={`form-input${nameError ? ' form-input--error' : ''}`}
                      placeholder="column_name"
                      {...register(`columns.${i}.name`)}
                    />
                    <input
                      className={`form-input${descError ? ' form-input--error' : ''}`}
                      placeholder="Description (min 20 chars)"
                      {...register(`columns.${i}.description`)}
                    />
                    <button
                      type="button"
                      className="btn btn--danger btn--sm"
                      onClick={() => removeColumn(i)}
                    >
                      ×
                    </button>
                  </div>
                  {(nameError || descError) && (
                    <div style={{ display: 'flex', gap: 16, marginTop: 4 }}>
                      {nameError && <div className="form-error">{nameError.message}</div>}
                      {descError && <div className="form-error">{descError.message}</div>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {step === 'questions' && (
          <div>
            <h3 style={{ fontWeight: 700, marginBottom: 16 }}>{t('wizard.steps.questions')}</h3>
            {questionFields.map((field, i) => {
              const questionError = errors.questions?.[i]?.question;
              const expectedSqlError = errors.questions?.[i]?.expected_sql;

              return (
                <div
                  key={field.id}
                  className="card card--elevated"
                  style={{ padding: 12, marginBottom: 16 }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      marginBottom: 8,
                    }}
                  >
                    <span className="text-sm fw-600 text-muted">Question #{i + 1}</span>
                    <button
                      type="button"
                      className="btn btn--danger btn--sm"
                      onClick={() => removeQuestion(i)}
                      style={{ padding: '2px 6px', fontSize: '11px' }}
                    >
                      Remove
                    </button>
                  </div>

                  <div className="form-group">
                    <label className="form-label">Question</label>
                    <input
                      className={`form-input${questionError ? ' form-input--error' : ''}`}
                      placeholder="How many orders were placed last month?"
                      {...register(`questions.${i}.question`)}
                    />
                    {questionError && <div className="form-error">{questionError.message}</div>}
                  </div>

                  <div className="form-group">
                    <label className="form-label">Expected SQL</label>
                    <textarea
                      className={`form-textarea${expectedSqlError ? ' form-textarea--error' : ''}`}
                      rows={2}
                      placeholder="SELECT COUNT(*) FROM ..."
                      {...register(`questions.${i}.expected_sql`)}
                    />
                    {expectedSqlError && (
                      <div className="form-error">{expectedSqlError.message}</div>
                    )}
                  </div>

                  <div className="form-group">
                    <label className="form-label">Difficulty</label>
                    <select className="form-select" {...register(`questions.${i}.difficulty`)}>
                      <option value="simple">Simple</option>
                      <option value="medium">Medium</option>
                      <option value="complex">Complex</option>
                    </select>
                  </div>

                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Question Type</label>
                    <select className="form-select" {...register(`questions.${i}.question_type`)}>
                      <option value="join">Join</option>
                      <option value="simple">Simple</option>
                      <option value="complex">Complex</option>
                      <option value="geo">Geo</option>
                      <option value="aggregate">Aggregate</option>
                      <option value="time_series">Time Series</option>
                    </select>
                  </div>
                </div>
              );
            })}
            <button type="button" className="btn btn--ghost btn--sm" onClick={addQuestion}>
              + Add Question
            </button>
          </div>
        )}

        {step === 'submit' && (
          <div style={{ textAlign: 'center', padding: '24px 0' }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Ready to submit!</div>
            <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>
              Table <strong>{createdTable?.name || watchOasisSourceId}</strong> with{' '}
              {watchColumns.length} columns and {watchQuestions.length} golden questions.
            </div>
          </div>
        )}
      </div>

      {/* Navigation */}
      <div className="flex gap-2" style={{ justifyContent: 'flex-end' }}>
        {currentStep > 0 && (
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => setCurrentStep((s) => s - 1)}
          >
            {t('wizard.back')}
          </button>
        )}
        <button
          type="button"
          className="btn btn--primary"
          disabled={
            createTableMutation.isPending ||
            createEnrichmentMutation.isPending ||
            createQuestionMutation.isPending ||
            submitError !== null
          }
          onClick={handleNext}
        >
          {step === 'questions' || step === 'submit' ? t('wizard.submit') : t('wizard.next')}
        </button>
      </div>
    </div>
  );
}
