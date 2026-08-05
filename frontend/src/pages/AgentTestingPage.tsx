import { memo, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { useMutation } from '@tanstack/react-query';
import { Alert, Button, Divider, Input, Modal, Select, Space, Spin, Switch, Tag } from 'antd';
import axios from 'axios';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity,
  AlertTriangle,
  Bot,
  Check,
  CheckCircle,
  CheckSquare,
  ChevronDown,
  ChevronUp,
  Clock,
  Code,
  Copy,
  Database,
  FileText,
  HelpCircle,
  Play,
  Send,
  Sparkles,
  Terminal,
  XCircle,
} from 'lucide-react';
import remarkGfm from 'remark-gfm';
import { v4 as uuidv4 } from 'uuid';

import { agentApi } from '../api/agent';
import { AgentGraph } from '../components/AgentGraph';
import { highlightJson, TraceTimeline } from '../components/TraceTimeline';

import type { ChatRequest, ChatResponse } from '../api/agent';

import styles from './AgentTestingPage.module.css';

const formatLabel = (str: string) => {
  return str
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
};

const stepDescriptions: Record<string, { label: string; desc: string }> = {
  validate_config: {
    label: 'Config Validation',
    desc: 'Verifying configuration settings and database connection health.',
  },
  init_flags: {
    label: 'Initialize Flags',
    desc: 'Loading active feature flags and system override settings.',
  },
  init_skills: {
    label: 'Load Skills',
    desc: 'Initializing specialized system skills and database rules.',
  },
  extractor: {
    label: 'Extract intent',
    desc: 'Parsing user query to extract table entities, filters, and fields.',
  },
  schema_explorer: {
    label: 'Explore Schema',
    desc: 'Exploring database catalog, matching candidates, and scanning schemas.',
  },
  query_builder: {
    label: 'Build Query',
    desc: 'Synthesizing proposed schema plan and building raw Trino SQL query.',
  },
  satisfaction_check: {
    label: 'Verify SQL',
    desc: 'Validating SQL syntax, schema references, and running dry execution tests.',
  },
  refiner: {
    label: 'Refine Query',
    desc: 'Applying correction feedback loops to optimize SQL structure.',
  },
  finalizer: {
    label: 'Finalize Output',
    desc: 'Compiling query explanation, formatting schema plan, and wrapping outputs.',
  },
};

const ThinkingProcess = memo(({ path }: { path: string[] }) => {
  const [expanded, setExpanded] = useState(true);

  return (
    <div
      style={{
        width: '100%',
        maxWidth: '600px',
        background: 'rgba(15, 23, 42, 0.4)',
        border: '1px solid rgba(255, 255, 255, 0.06)',
        borderRadius: '12px',
        overflow: 'hidden',
        marginTop: 16,
        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.25)',
        backdropFilter: 'blur(8px)',
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          width: '100%',
          padding: '12px 16px',
          background: 'rgba(255, 255, 255, 0.02)',
          border: 'none',
          color: 'var(--text-muted)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          cursor: 'pointer',
          fontSize: '13px',
          fontWeight: 500,
          borderBottom: expanded ? '1px solid rgba(255, 255, 255, 0.06)' : 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Activity size={14} className={styles.pulseIcon} />
          <span>
            Agent Thought Process{' '}
            {path.length > 0 ? `(${path.length} steps executed)` : '(Initializing...)'}
          </span>
        </div>
        {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>

      {expanded && (
        <div
          style={{
            padding: '16px 20px',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            maxHeight: '250px',
            overflowY: 'auto',
          }}
        >
          {path.length === 0 ? (
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <div style={{ marginTop: 2 }}>
                <div className={styles.pulseDot} />
              </div>
              <div style={{ textAlign: 'left' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, color: '#38BDF8' }}>
                  Initializing Agent
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: 2 }}>
                  Establishing session and validating workspace configurations.
                </div>
              </div>
            </div>
          ) : (
            path.map((step, idx) => {
              const isActive = idx === path.length - 1;
              const meta = stepDescriptions[step] || {
                label: formatLabel(step),
                desc: 'Executing agent step node.',
              };
              return (
                <div key={idx} style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                  <div style={{ marginTop: 2 }}>
                    {isActive ? (
                      <div className={styles.pulseDot} />
                    ) : (
                      <CheckCircle size={15} color="#10B981" style={{ display: 'block' }} />
                    )}
                  </div>
                  <div style={{ textAlign: 'left' }}>
                    <div
                      style={{
                        fontSize: '13px',
                        fontWeight: 600,
                        color: isActive ? '#38BDF8' : 'var(--text-h)',
                      }}
                    >
                      {meta.label}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: 2 }}>
                      {meta.desc}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
});

const CollapsibleTraceBlock = ({
  label,
  content,
  themeColor,
  defaultExpanded = true,
}: {
  label: string;
  content: string;
  themeColor: string;
  defaultExpanded?: boolean;
}) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const trimmed = content?.trim() || '';
  const isJson =
    (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'));

  let formattedContent = content;
  if (isJson) {
    try {
      const parsed = JSON.parse(trimmed);
      formattedContent = JSON.stringify(parsed, null, 2);
    } catch (e) {
      // ignore
    }
  }

  const jsonHighlightStyles = `
    .json-key { color: #818CF8; font-weight: 600; }
    .json-string { color: #34D399; }
    .json-number { color: #FB923C; }
    .json-boolean { color: #F472B6; font-weight: 500; }
    .json-null { color: #64748B; font-style: italic; }
  `;

  return (
    <div
      style={{
        border: `1px solid rgba(255, 255, 255, 0.08)`,
        borderRadius: '8px',
        overflow: 'hidden',
        background: 'rgba(15, 23, 42, 0.3)',
        boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
      }}
    >
      <style dangerouslySetInnerHTML={{ __html: jsonHighlightStyles }} />
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '12px 16px',
          background: 'rgba(255, 255, 255, 0.02)',
          borderBottom: expanded ? '1px solid rgba(255, 255, 255, 0.06)' : 'none',
          cursor: 'pointer',
          userSelect: 'none',
          transition: 'background 0.2s',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div
            style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              backgroundColor: themeColor,
              boxShadow: `0 0 8px ${themeColor}`,
            }}
          />
          <span
            style={{
              fontSize: '12px',
              fontWeight: 700,
              color: 'var(--text-h)',
              letterSpacing: '0.5px',
            }}
          >
            {label}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <button
            onClick={handleCopy}
            style={{
              background: 'rgba(255, 255, 255, 0.04)',
              border: '1px solid rgba(255, 255, 255, 0.08)',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: '11px',
              fontWeight: 500,
              padding: '4px 10px',
              borderRadius: '6px',
              transition: 'all 0.2s',
            }}
          >
            {copied ? <Check size={12} color="#10B981" /> : <Copy size={12} />}
            <span>{copied ? 'Copied!' : 'Copy'}</span>
          </button>
          <button
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              padding: 0,
            }}
          >
            {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
        </div>
      </div>

      {expanded && (
        <pre
          style={{
            margin: 0,
            padding: '16px',
            fontSize: '13px',
            fontFamily: 'var(--mono), monospace',
            color: '#E2E8F0',
            overflowX: 'auto',
            maxHeight: '400px',
            background: '#0B0F19',
            textAlign: 'left',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
            lineHeight: '1.5',
            borderLeft: `3px solid ${themeColor}`,
          }}
        >
          {isJson ? (
            <code dangerouslySetInnerHTML={{ __html: highlightJson(formattedContent) }} />
          ) : (
            formattedContent
          )}
        </pre>
      )}
    </div>
  );
};

// ----------------------------------------------------------------------
// AgentTestingHeader
// ----------------------------------------------------------------------
const AgentTestingHeader = memo(
  ({
    hitlEnabled,
    setHitlEnabled,
    connections,
    selectedConnection,
    setSelectedConnection,
    loadingConnections,
  }: {
    hitlEnabled: boolean;
    setHitlEnabled: (v: boolean) => void;
    connections: any[];
    selectedConnection: number | undefined;
    setSelectedConnection: (v: number | undefined) => void;
    loadingConnections: boolean;
  }) => (
    <div className={styles.header}>
      <div>
        <h1 className={styles.title}>
          <Bot size={28} color="var(--accent)" />
          Text2SQL Agent Sandbox
        </h1>
        <p className={styles.subtitle}>
          Test the agent directly. Toggle human-in-the-loop to approve or reject the agent's work.
        </p>
      </div>
      <div className={styles.controls}>
        <div className={styles.controlItem}>
          <span>Human in the Loop</span>
          <Switch checked={hitlEnabled} onChange={setHitlEnabled} />
        </div>
        <div className={styles.controlItem}>
          <span>Catalog</span>
          <Select
            value={selectedConnection}
            onChange={setSelectedConnection}
            style={{ minWidth: 200 }}
            placeholder="Select connection"
            loading={loadingConnections}
            options={connections.map((c: any) => ({
              value: c.connection_id,
              label: c.name,
            }))}
          />
        </div>
      </div>
    </div>
  ),
);
AgentTestingHeader.displayName = 'AgentTestingHeader';

// ----------------------------------------------------------------------
// AgentChatInput
// ----------------------------------------------------------------------
const AgentChatInput = ({
  query,
  setQuery,
  onSubmit,
  disabled,
  loading,
  submitDisabled,
}: {
  query: string;
  setQuery: (q: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  loading: boolean;
  submitDisabled?: boolean;
}) => (
  <div className={`${styles.glassCard} ${styles.animateIn}`}>
    <Space.Compact className={styles.chatInputWrapper}>
      <Input
        className={styles.glowInput}
        placeholder="Ask the agent to query a table..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onPressEnter={(e) => {
          if (!disabled && !submitDisabled) onSubmit();
        }}
        disabled={disabled}
      />
      <Button
        className={styles.primaryButton}
        onClick={onSubmit}
        loading={loading}
        disabled={disabled || submitDisabled}
      >
        {!loading && <Play size={18} />}
        Run Agent
      </Button>
    </Space.Compact>
  </div>
);

// ----------------------------------------------------------------------
// AgentApprovalForm
// ----------------------------------------------------------------------
const AgentApprovalForm = ({
  chatResponse,
  threadId,
  isResuming,
  onApprove,
  onReject,
}: {
  chatResponse: ChatResponse;
  threadId: string;
  isResuming: boolean;
  onApprove: (resumeValue?: any) => void;
  onReject: (feedback: string, category?: string) => void;
}) => {
  const [rejectionCategory, setRejectionCategory] = useState<string | undefined>(undefined);
  const [suggestedFixes, setSuggestedFixes] = useState<string[]>([]);
  const [loadingFixes, setLoadingFixes] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [copied, setCopied] = useState(false);

  const interrupt = chatResponse.interrupt_details || {};
  const interruptType = interrupt.type;

  const sqlQuery = chatResponse.sql_query || (interrupt.sql_query as string) || '';
  const sqlExplanation =
    chatResponse.sql_explanation || (interrupt.sql_explanation as string) || '';

  const handleCopy = () => {
    if (sqlQuery) {
      navigator.clipboard.writeText(sqlQuery);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleCategoryChange = (val: string) => {
    setRejectionCategory(val);
    if (val && threadId) {
      setLoadingFixes(true);
      agentApi
        .suggestFixes(threadId, val)
        .then(setSuggestedFixes)
        .finally(() => setLoadingFixes(false));
    } else {
      setSuggestedFixes([]);
    }
  };

  // Ambiguity Resolution UI
  if (interruptType === 'schema_explorer_ambiguity') {
    const message =
      (interrupt.message as string) ||
      'We found multiple tables matching your query. Please select the correct option:';
    const options = (interrupt.options as string[]) || [];

    return (
      <motion.div
        className={styles.interruptCardAmbiguity}
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <div className={styles.interruptHeaderAmbiguity}>
          <HelpCircle size={22} className={styles.iconAmbiguity} />
          <div className={styles.interruptHeaderTitles}>
            <span className={styles.interruptBadgeAmbiguity}>Ambiguity Detected</span>
            <h3 className={styles.interruptTitleText}>Schema Clarification Required</h3>
          </div>
        </div>

        <div className={styles.ambiguityMessage}>{message}</div>

        <div className={styles.ambiguityOptionsList}>
          {options.map((opt) => (
            <button
              key={opt}
              className={styles.ambiguityOptionBtn}
              onClick={() => onApprove(opt)}
              disabled={isResuming}
            >
              <Database size={16} style={{ opacity: 0.7 }} />
              <span>{opt}</span>
            </button>
          ))}
        </div>
      </motion.div>
    );
  }

  // Escalation / Failure UI
  if (interruptType === 'hitl_escalation') {
    const reason =
      (interrupt.reason as string) ||
      'The agent hit a loop or failed to solve the request automatically.';
    return (
      <motion.div
        className={styles.interruptCardEscalation}
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <div className={styles.interruptHeaderEscalation}>
          <AlertTriangle size={22} className={styles.iconEscalation} />
          <div className={styles.interruptHeaderTitles}>
            <span className={styles.interruptBadgeEscalation}>Escalation Fallback</span>
            <h3 className={styles.interruptTitleText}>Automatic Execution Paused</h3>
          </div>
        </div>

        <div className={styles.escalationReasonBox}>
          <strong>Reason for pause:</strong>
          <p>{reason}</p>
        </div>

        <Divider style={{ borderColor: 'rgba(239, 68, 68, 0.15)' }} />

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500, color: 'var(--text-muted)' }}>
              Help the agent correct course:
            </div>
            <Input.TextArea
              className={styles.glowInput}
              rows={3}
              placeholder="Provide corrections (e.g. 'Use the customers table instead of clients', or specify the exact query logic)..."
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
          </div>

          <div className={styles.actionRow} style={{ justifyContent: 'flex-end' }}>
            <Button
              className={styles.escalationSubmitBtn}
              onClick={() => onReject(feedback, 'Manual Override')}
              disabled={!feedback || isResuming}
              loading={isResuming}
            >
              Inject Feedback & Resume
            </Button>
          </div>
        </div>
      </motion.div>
    );
  }

  // Query Approval (Verification Gateway) UI
  return (
    <motion.div
      className={styles.interruptCardApproval}
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className={styles.interruptHeaderApproval}>
        <CheckSquare size={22} className={styles.iconApproval} />
        <div className={styles.interruptHeaderTitles}>
          <span className={styles.interruptBadgeApproval}>Verification Gateway</span>
          <h3 className={styles.interruptTitleText}>Review Generated SQL Logic</h3>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 24, marginTop: 16 }}>
        {sqlQuery && (
          <div>
            <div
              style={{
                marginBottom: 8,
                fontWeight: 600,
                color: 'var(--text-h)',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <Code size={15} color="#faad14" />
              <span>Generated SQL Query</span>
            </div>
            <div className={styles.codeBlockWrapper}>
              <div className={styles.codeHeader}>
                <span className={styles.codeLang}>SQL (Trino dialect)</span>
                <button className={styles.copyBtn} onClick={handleCopy}>
                  {copied ? <Check size={14} color="#4ade80" /> : <Copy size={14} />}
                  <span>{copied ? 'Copied!' : 'Copy Query'}</span>
                </button>
              </div>
              <pre className={styles.codeBlock}>{sqlQuery}</pre>
            </div>

            {sqlExplanation && (
              <div className={styles.explanationWrapper} style={{ marginTop: 12 }}>
                <div className={styles.explanationHeader}>
                  <Sparkles size={16} />
                  <span>SQL Explanation / Logic</span>
                </div>
                <div className="markdown-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{sqlExplanation}</ReactMarkdown>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <Divider style={{ borderColor: 'rgba(250, 173, 20, 0.15)', margin: '24px 0' }} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div
              style={{ marginBottom: 6, fontWeight: 500, fontSize: 13, color: 'var(--text-muted)' }}
            >
              Rejection Category (If rejecting)
            </div>
            <Select
              style={{ width: '100%' }}
              placeholder="Select rejection reason..."
              allowClear
              value={rejectionCategory}
              onChange={handleCategoryChange}
              options={[
                { label: 'Wrong Tables', value: 'Wrong Tables' },
                { label: 'Wrong Logic', value: 'Wrong Logic' },
                { label: 'Other', value: 'Other' },
              ]}
            />
          </div>

          <div>
            <div
              style={{ marginBottom: 6, fontWeight: 500, fontSize: 13, color: 'var(--text-muted)' }}
            >
              Quick Feedback Suggestions
            </div>
            {loadingFixes ? (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', paddingTop: 6 }}>
                <Spin size="small" /> Generating...
              </div>
            ) : suggestedFixes.length > 0 ? (
              <Space wrap size={[4, 4]}>
                {suggestedFixes.map((fix) => (
                  <Button
                    key={fix}
                    size="small"
                    onClick={() => setFeedback(fix)}
                    style={{ fontSize: 11 }}
                  >
                    {fix}
                  </Button>
                ))}
              </Space>
            ) : (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', paddingTop: 6 }}>
                Select category to generate quick fixes
              </div>
            )}
          </div>
        </div>

        <div>
          <div
            style={{ marginBottom: 6, fontWeight: 500, fontSize: 13, color: 'var(--text-muted)' }}
          >
            Feedback / Correction Instructions
          </div>
          <Input.TextArea
            className={styles.glowInput}
            rows={2}
            placeholder="Add specific comments about what to correct..."
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
          />
        </div>

        <div className={styles.actionRow} style={{ justifyContent: 'flex-end', marginTop: 8 }}>
          <Button
            className={styles.rejectButton}
            icon={<XCircle size={16} />}
            onClick={() => onReject(feedback, rejectionCategory)}
            disabled={(!feedback && !rejectionCategory) || isResuming}
            loading={isResuming}
          >
            Reject
          </Button>
          <Button
            className={styles.primaryButton}
            icon={<CheckCircle size={16} color="#020617" />}
            onClick={() => onApprove({ approved: true })}
            disabled={isResuming}
            loading={isResuming}
          >
            Approve & Execute
          </Button>
        </div>
      </div>
    </motion.div>
  );
};

// ----------------------------------------------------------------------
// AgentResultDisplay
// ----------------------------------------------------------------------
const AgentResultDisplay = ({
  chatResponse,
  onReset,
  onViewTrace,
}: {
  chatResponse: ChatResponse;
  onReset: () => void;
  onViewTrace: () => void;
}) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (chatResponse.sql_query) {
      navigator.clipboard.writeText(chatResponse.sql_query);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <motion.div
      className={styles.completedCard}
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div className={styles.completedHeader}>
        <div
          className={styles.successIconWrapper}
          style={
            chatResponse.is_unanswerable
              ? { background: 'rgba(239, 68, 68, 0.1)', borderColor: 'rgba(239, 68, 68, 0.2)' }
              : undefined
          }
        >
          {chatResponse.is_unanswerable ? (
            <XCircle size={32} color="#ef4444" className={styles.successIcon} />
          ) : (
            <CheckCircle size={32} className={styles.successIcon} />
          )}
        </div>
        <div className={styles.completedHeaderTitles}>
          <span
            className={styles.completedStatus}
            style={chatResponse.is_unanswerable ? { color: '#ef4444' } : undefined}
          >
            {chatResponse.is_unanswerable ? 'UNANSWERABLE' : 'TASK COMPLETED'}
          </span>
          <h2 className={styles.completedTitle}>
            {chatResponse.is_unanswerable ? 'Agent Could Not Answer' : 'Agent Execution Successful'}
          </h2>
        </div>
        <Tag
          color={chatResponse.is_unanswerable ? 'error' : 'success'}
          className={styles.completedTag}
        >
          {chatResponse.is_unanswerable ? 'Failed' : 'Done'}
        </Tag>
      </div>

      <div className={styles.completedGrid}>
        {chatResponse.summary && (
          <div className={styles.resultSection}>
            <div className={styles.resultSectionHeader}>
              <FileText size={18} className={styles.sectionIcon} />
              <span>Summary of Results</span>
            </div>
            <div className={styles.summaryContent}>
              <div className="markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{chatResponse.summary}</ReactMarkdown>
              </div>
            </div>
          </div>
        )}

        {chatResponse.sql_query && (
          <div className={styles.resultSection}>
            <div className={styles.resultSectionHeader}>
              <Terminal size={18} className={styles.sectionIcon} />
              <span>Generated SQL Query</span>
            </div>
            <div className={styles.codeBlockWrapper}>
              <div className={styles.codeHeader}>
                <span className={styles.codeLang}>SQL (Trino dialect)</span>
                <button className={styles.copyBtn} onClick={handleCopy}>
                  {copied ? <Check size={14} color="#4ade80" /> : <Copy size={14} />}
                  <span>{copied ? 'Copied!' : 'Copy Query'}</span>
                </button>
              </div>
              <pre className={styles.codeBlock}>{chatResponse.sql_query}</pre>
            </div>

            {chatResponse.sql_explanation && (
              <div className={styles.explanationWrapper}>
                <div className={styles.explanationHeader}>
                  <Sparkles size={16} />
                  <span>SQL Explanation</span>
                </div>
                <div className="markdown-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {chatResponse.sql_explanation}
                  </ReactMarkdown>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className={styles.metadataGrid}>
        {chatResponse.raw_data_ref && (
          <div className={styles.metaItem}>
            <span className={styles.metaLabel}>Data Reference:</span>
            <span className={styles.metaValue}>{chatResponse.raw_data_ref}</span>
          </div>
        )}
        {chatResponse.thread_id && (
          <div className={styles.metaItem}>
            <span className={styles.metaLabel}>Session ID:</span>
            <span className={styles.metaValue}>{chatResponse.thread_id}</span>
          </div>
        )}
      </div>

      <Divider className={styles.completedDivider} />

      <div className={styles.completedActions}>
        <Button
          type="primary"
          onClick={onReset}
          icon={<Send size={16} />}
          className={styles.completedBtnPrimary}
        >
          Start New Request
        </Button>
        <Button
          onClick={onViewTrace}
          icon={<Activity size={16} />}
          className={styles.completedBtnSecondary}
        >
          View Full Trace
        </Button>
      </div>
    </motion.div>
  );
};

// ----------------------------------------------------------------------
// Main Page
// ----------------------------------------------------------------------
export function AgentTestingPage() {
  const [query, setQuery] = useState('');
  const [hitlEnabled, setHitlEnabled] = useState(true);
  const [connections, setConnections] = useState<any[]>([]);
  const [selectedConnection, setSelectedConnection] = useState<number | undefined>(undefined);
  const [loadingConnections, setLoadingConnections] = useState(false);

  useEffect(() => {
    setLoadingConnections(true);
    agentApi
      .listConnections()
      .then((res) => {
        setConnections(res.connections || []);
        setLoadingConnections(false);
      })
      .catch((err) => {
        console.error('Failed to fetch connections:', err);
        setLoadingConnections(false);
      });
  }, []);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [chatResponse, setChatResponse] = useState<ChatResponse | null>(null);
  const [traceModalVisible, setTraceModalVisible] = useState(false);

  const [traceSpans, setTraceSpans] = useState<any[]>([]);
  const [loadingTrace, setLoadingTrace] = useState(false);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [selectedStepIndex, setSelectedStepIndex] = useState<number | null>(null);
  const [executionPath, setExecutionPath] = useState<string[]>([]);

  useEffect(() => {
    if (!traceId) {
      setTraceSpans([]);
      setSelectedStep(null);
      setSelectedStepIndex(null);
      return;
    }

    setLoadingTrace(true);
    let attempts = 0;
    const maxAttempts = 5;
    let timeoutId: ReturnType<typeof setTimeout>;
    let isSubscribed = true;

    const fetchTrace = () => {
      if (!isSubscribed) return;
      axios
        .get(`/api/agent/traces/${traceId}`)
        .then((res) => {
          if (!isSubscribed) return;
          const data = res.data || [];
          if (data.length > 0) {
            setTraceSpans(data);
            setLoadingTrace(false);
          } else if (attempts < maxAttempts) {
            attempts++;
            timeoutId = setTimeout(fetchTrace, 2000); // retry in 2s
          } else {
            setTraceSpans([]);
            setLoadingTrace(false);
          }
        })
        .catch((err) => {
          if (!isSubscribed) return;
          console.error('Failed to fetch trace', err);
          if (attempts < maxAttempts) {
            attempts++;
            timeoutId = setTimeout(fetchTrace, 2000);
          } else {
            setLoadingTrace(false);
          }
        });
    };

    fetchTrace();

    return () => {
      isSubscribed = false;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [traceId]);

  const chatMutation = useMutation({
    mutationFn: (req: ChatRequest) => agentApi.chat(req),
    onSuccess: (data) => {
      if (data.trace_id) {
        setTraceId(data.trace_id);
      }
      setChatResponse(data);
      if (data.execution_path) {
        setExecutionPath(data.execution_path);
      }
    },
  });

  useEffect(() => {
    if (!threadId) return;

    const eventSource = new EventSource(`/api/agent/stream/${threadId}`);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.node) {
          setExecutionPath((prev) => {
            if (prev.length > 0 && prev[prev.length - 1] === data.node) {
              return prev;
            }
            return [...prev, data.node];
          });
        }
      } catch (err) {
        console.error('SSE parse error:', err);
      }
    };

    eventSource.onerror = (err) => {
      console.error('SSE connection error:', err);
      eventSource.close();
    };

    return () => {
      eventSource.close();
    };
  }, [threadId]);

  const isResuming = chatMutation.isPending && chatResponse?.status === 'interrupted';
  const isInputDisabled = chatMutation.isPending || chatResponse?.status === 'interrupted';

  const handleSubmit = () => {
    if (!query || !selectedConnection) return;
    setChatResponse(null);
    const newThreadId = uuidv4();
    setThreadId(newThreadId);
    setExecutionPath([]);
    setTraceId(null);
    setSelectedStep(null);
    setSelectedStepIndex(null);

    // Delay mutation slightly to allow SSE EventSource to connect
    setTimeout(() => {
      chatMutation.mutate({
        query,
        thread_id: newThreadId,
        hitl_enabled: hitlEnabled,
        connection_id: selectedConnection,
      });
    }, 300);
  };

  const handleApprove = (resumeValue?: any) => {
    if (!threadId) return;
    setTimeout(() => {
      chatMutation.mutate({
        thread_id: threadId,
        resume_value: resumeValue !== undefined ? resumeValue : { approved: true },
        connection_id: selectedConnection,
      });
    }, 300);
  };

  const handleReject = (feedback: string, category?: string) => {
    if (!threadId) return;
    setTimeout(() => {
      chatMutation.mutate({
        thread_id: threadId,
        resume_value: { approved: false, feedback, rejection_category: category },
        hitl_enabled: hitlEnabled,
        connection_id: selectedConnection,
      });
    }, 300);
  };

  const handleReset = () => {
    setQuery('');
    setThreadId(null);
    setExecutionPath([]);
    setTraceId(null);
    setChatResponse(null);
    setSelectedStep(null);
    setSelectedStepIndex(null);
    chatMutation.reset();
  };

  const handleNodeClick = (nodeName: string, index: number) => {
    setSelectedStep(nodeName);
    setSelectedStepIndex(index);
  };

  const getSelectedSpan = () => {
    if (!selectedStep || selectedStepIndex === null) return null;
    const target = selectedStep.toLowerCase().replace(/_/g, '');
    const matches = traceSpans.filter((s) => {
      const name = s.span_name.toLowerCase().replace(/_/g, '');
      return name.includes(target) || target.includes(name);
    });

    const path = chatResponse?.execution_path || [];
    let occurIndex = 0;
    for (let idx = 0; idx < selectedStepIndex; idx++) {
      if (path[idx] === selectedStep) {
        occurIndex++;
      }
    }

    return matches[occurIndex] || matches[matches.length - 1] || null;
  };

  const selectedSpan = getSelectedSpan();

  const formatLabel = (str: string) => {
    return str
      .split('_')
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
  };

  const isPendingInitial = chatMutation.isPending && !chatResponse;

  return (
    <div className={styles.agentTestingPage}>
      <AgentTestingHeader
        hitlEnabled={hitlEnabled}
        setHitlEnabled={setHitlEnabled}
        connections={connections}
        selectedConnection={selectedConnection}
        setSelectedConnection={setSelectedConnection}
        loadingConnections={loadingConnections}
      />

      <AgentChatInput
        query={query}
        setQuery={setQuery}
        onSubmit={handleSubmit}
        disabled={isInputDisabled}
        loading={isPendingInitial}
        submitDisabled={!selectedConnection || !query.trim()}
      />

      {chatMutation.isError && (
        <Alert
          type="error"
          showIcon
          message="Agent Error"
          description={
            chatMutation.error?.message || 'An error occurred while communicating with the agent.'
          }
          style={{ marginBottom: 24, borderRadius: 8 }}
        />
      )}

      {(isResuming || isPendingInitial) && (
        <div className={styles.spinnerWrapper}>
          <div className={styles.customSpinner} />
          <div className={styles.spinnerText}>
            {isResuming ? 'Resuming agent execution...' : 'Agent is analyzing request...'}
          </div>
          <ThinkingProcess path={executionPath} />
        </div>
      )}

      <AnimatePresence mode="wait">
        {(chatMutation.isPending || chatResponse || threadId) && (
          <motion.div
            key="graph"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            style={{ marginBottom: 24 }}
          >
            <AgentGraph
              threadId={threadId!}
              executionPath={executionPath}
              onNodeClick={handleNodeClick}
            />
          </motion.div>
        )}
      </AnimatePresence>

      <Modal
        title={
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              width: '95%',
              color: 'var(--text-h)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {selectedSpan?.status === 'error' ? (
                <XCircle size={20} color="#ef4444" />
              ) : (
                <CheckCircle size={20} color="#10b981" />
              )}
              <div>
                <h4
                  style={{ margin: 0, fontSize: '18px', fontWeight: 600, color: 'var(--text-h)' }}
                >
                  {selectedStep ? formatLabel(selectedStep) : ''}
                </h4>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  Step {(selectedStepIndex ?? 0) + 1} of execution
                </span>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, marginRight: 24 }}>
              {selectedSpan?.model !== 'N/A' && selectedSpan?.model && (
                <Tag color="purple">{selectedSpan.model}</Tag>
              )}
              {selectedSpan?.duration_ms !== undefined && (
                <Tag color="default" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Clock size={12} />
                  <span>{selectedSpan.duration_ms}ms</span>
                </Tag>
              )}
            </div>
          </div>
        }
        open={!!selectedStep}
        onCancel={() => {
          setSelectedStep(null);
          setSelectedStepIndex(null);
        }}
        footer={[
          <Button
            key="close"
            onClick={() => {
              setSelectedStep(null);
              setSelectedStepIndex(null);
            }}
            style={{
              background: 'rgba(255, 255, 255, 0.05)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: 'var(--text-h)',
            }}
          >
            Close Details
          </Button>,
        ]}
        width={750}
        styles={{
          content: {
            background: '#0F172A',
            border: '1px solid #1E293B',
            borderRadius: '16px',
            boxShadow: '0 20px 25px -5px rgb(0 0 0 / 0.5), 0 8px 10px -6px rgb(0 0 0 / 0.5)',
          },
          header: {
            background: 'transparent',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            paddingBottom: '14px',
            marginBottom: '16px',
          },
        }}
      >
        <div style={{ marginTop: 16 }}>
          {selectedSpan ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {selectedSpan.input_preview && (
                <CollapsibleTraceBlock
                  label="INPUT"
                  content={selectedSpan.input_preview}
                  themeColor="#38BDF8"
                />
              )}
              {selectedSpan.output_preview && (
                <CollapsibleTraceBlock
                  label="OUTPUT"
                  content={selectedSpan.output_preview}
                  themeColor="#10B981"
                />
              )}
            </div>
          ) : (
            <div
              style={{
                color: 'var(--text-muted)',
                fontSize: 13,
                textAlign: 'center',
                padding: '24px 0',
              }}
            >
              {loadingTrace ? (
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: 12,
                  }}
                >
                  <Spin />
                  <span>Loading execution logs from Langfuse...</span>
                </div>
              ) : (
                'No trace logs available for this step yet.'
              )}
            </div>
          )}
        </div>
      </Modal>

      <AnimatePresence mode="wait">
        {chatResponse && !chatMutation.isPending && (
          <motion.div key="result" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            {chatResponse.status === 'interrupted' ? (
              <AgentApprovalForm
                chatResponse={chatResponse}
                threadId={threadId!}
                isResuming={isResuming}
                onApprove={handleApprove}
                onReject={handleReject}
              />
            ) : (
              <AgentResultDisplay
                chatResponse={chatResponse}
                onReset={handleReset}
                onViewTrace={() => setTraceModalVisible(true)}
              />
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <Modal
        title={<span style={{ color: 'var(--text-h)' }}>Execution Trace</span>}
        open={traceModalVisible}
        onCancel={() => setTraceModalVisible(false)}
        footer={null}
        width={800}
        styles={{
          content: { background: '#0F172A', border: '1px solid #1E293B' },
          header: { background: 'transparent' },
        }}
      >
        {traceId && <TraceTimeline traceId={traceId} />}
        {!traceId && <div style={{ color: 'var(--text-muted)' }}>No trace data available.</div>}
      </Modal>
    </div>
  );
}
