import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { useMutation } from '@tanstack/react-query';
import { Alert, Button, Card, Divider, Input, Modal, Select, Space, Spin, Switch, Tag } from 'antd';
import { Bot, CheckCircle, Play, Send, ShieldAlert, XCircle } from 'lucide-react';
import remarkGfm from 'remark-gfm';

import { agentApi } from '../api/agent';
import { AgentGraph } from '../components/AgentGraph';
import { TraceTimeline } from '../components/TraceTimeline';

import type { ChatRequest, ChatResponse } from '../api/agent';

export function AgentTestingPage() {
  const [query, setQuery] = useState('');
  const [hitlEnabled, setHitlEnabled] = useState(true);
  const [allowedStatuses, setAllowedStatuses] = useState<string[]>(['production']);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [chatResponse, setChatResponse] = useState<ChatResponse | null>(null);
  const [feedback, setFeedback] = useState('');
  const [rejectionCategory, setRejectionCategory] = useState<string | undefined>(undefined);
  const [suggestedFixes, setSuggestedFixes] = useState<string[]>([]);
  const [loadingFixes, setLoadingFixes] = useState(false);
  const [traceModalVisible, setTraceModalVisible] = useState(false);

  const chatMutation = useMutation({
    mutationFn: (req: ChatRequest) => agentApi.chat(req),
    onSuccess: (data) => {
      setThreadId(data.thread_id);
      setChatResponse(data);
    },
  });

  const isResuming = chatMutation.isPending && chatResponse?.status === 'interrupted';

  const handleSubmit = () => {
    if (!query) return;
    setChatResponse(null);
    chatMutation.mutate({
      query,
      hitl_enabled: hitlEnabled,
      allowed_statuses: allowedStatuses.length > 0 ? allowedStatuses : undefined,
    });
  };

  const handleApprove = () => {
    if (!threadId) return;
    chatMutation.mutate({
      thread_id: threadId,
      resume_value: { approved: true },
    });
  };

  const handleReject = () => {
    if (!threadId) return;
    chatMutation.mutate({
      thread_id: threadId,
      resume_value: { approved: false, feedback, rejection_category: rejectionCategory },
      hitl_enabled: hitlEnabled,
    });
  };

  const handleReset = () => {
    setQuery('');
    setThreadId(null);
    setChatResponse(null);
    setFeedback('');
    chatMutation.reset();
  };

  return (
    <div className="page" style={{ maxWidth: 800, margin: '0 auto', paddingTop: 20 }}>
      <div className="page__header" style={{ marginBottom: 20 }}>
        <div>
          <h1 className="page__title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Bot size={24} color="var(--accent-primary)" />
            Text2SQL Agent Sandbox
          </h1>
          <p className="page__subtitle">
            Test the agent directly. Toggle human-in-the-loop to approve or reject the agent's work.
          </p>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>Human in the Loop</span>
            <Switch checked={hitlEnabled} onChange={setHitlEnabled} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>Table Status</span>
            <Select
              mode="multiple"
              value={allowedStatuses}
              onChange={setAllowedStatuses}
              style={{ minWidth: 200 }}
              placeholder="Select allowed statuses"
              options={[
                { value: 'production', label: 'Production' },
                { value: 'verified', label: 'Verified' },
                { value: 'sandbox', label: 'Sandbox' },
                { value: 'draft', label: 'Draft' },
                { value: 'degraded', label: 'Degraded' },
              ]}
            />
          </div>
        </div>
      </div>

      <Card
        style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border-color)',
          borderRadius: 8,
          marginBottom: 20,
        }}
      >
        <Space.Compact style={{ width: '100%' }}>
          <Input
            size="large"
            placeholder="Ask the agent to query a table..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onPressEnter={handleSubmit}
            disabled={chatMutation.isPending || chatResponse?.status === 'interrupted'}
          />
          <Button
            type="primary"
            size="large"
            icon={<Play size={16} />}
            onClick={handleSubmit}
            loading={chatMutation.isPending && !chatResponse}
            disabled={chatMutation.isPending || chatResponse?.status === 'interrupted'}
          >
            Run Agent
          </Button>
        </Space.Compact>
      </Card>

      {chatMutation.isError && (
        <Alert
          type="error"
          showIcon
          message="Agent Error"
          description={
            chatMutation.error?.message || 'An error occurred while communicating with the agent.'
          }
          style={{ marginBottom: 20 }}
        />
      )}

      {chatMutation.isPending && chatResponse?.status === 'interrupted' && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="large" />
          <div style={{ marginTop: 16, color: 'var(--text-muted)' }}>Agent is processing...</div>
        </div>
      )}

      {(chatMutation.isPending || chatResponse || threadId) && (
        <div style={{ marginBottom: 20 }}>
          <AgentGraph threadId={threadId} />
        </div>
      )}

      {chatResponse && !chatMutation.isPending && (
        <div className="agent-result">
          {chatResponse.status === 'interrupted' && (
            <Card
              title={
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <ShieldAlert size={18} color="#faad14" /> Agent Needs Approval
                </span>
              }
              style={{
                border: '1px solid #faad14',
                background: 'rgba(250, 173, 20, 0.05)',
              }}
            >
              <div style={{ marginBottom: 20 }}>
                <h4 style={{ margin: '0 0 8px 0', color: 'var(--text-color)' }}>
                  Interrupt Details
                </h4>
                <pre
                  style={{
                    background: 'rgba(0,0,0,0.2)',
                    padding: 12,
                    borderRadius: 6,
                    fontSize: 12,
                    overflowX: 'auto',
                    margin: 0,
                  }}
                >
                  {JSON.stringify(chatResponse.interrupt_details, null, 2)}
                </pre>
              </div>

              {(chatResponse.sql_query || chatResponse.schema_plan) && (
                <div style={{ marginBottom: 20 }}>
                  <h4 style={{ margin: '0 0 8px 0', color: 'var(--text-color)' }}>Current State</h4>
                  {chatResponse.schema_plan && (
                    <div style={{ marginBottom: 10 }}>
                      <strong>Schema Plan:</strong>
                      <div
                        className="markdown-body"
                        style={{ fontSize: 13, color: 'var(--text-muted)' }}
                      >
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {chatResponse.schema_plan}
                        </ReactMarkdown>
                      </div>
                    </div>
                  )}
                  {chatResponse.sql_query && (
                    <div>
                      <strong>SQL Query:</strong>
                      <pre
                        style={{
                          background: 'rgba(0,0,0,0.2)',
                          padding: 12,
                          borderRadius: 6,
                          fontSize: 12,
                          overflowX: 'auto',
                          margin: 0,
                          color: '#56b6c2',
                        }}
                      >
                        {chatResponse.sql_query}
                      </pre>
                    </div>
                  )}
                </div>
              )}

              <Divider style={{ borderColor: 'rgba(250, 173, 20, 0.2)' }} />

              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div>
                  <div style={{ marginBottom: 4, fontWeight: 500, fontSize: 13 }}>Rejection Category</div>
                  <Select
                    style={{ width: '100%' }}
                    placeholder="Select rejection reason..."
                    allowClear
                    value={rejectionCategory}
                    onChange={(val) => {
                      setRejectionCategory(val);
                      if (val && threadId) {
                        setLoadingFixes(true);
                        agentApi.suggestFixes(threadId, val)
                          .then(setSuggestedFixes)
                          .finally(() => setLoadingFixes(false));
                      } else {
                        setSuggestedFixes([]);
                      }
                    }}
                    options={[
                      { label: 'Wrong Tables', value: 'Wrong Tables' },
                      { label: 'Wrong Logic', value: 'Wrong Logic' },
                      { label: 'Other', value: 'Other' },
                    ]}
                  />
                </div>

                {loadingFixes && <div style={{ fontSize: 12, color: 'var(--text-muted)' }}><Spin size="small" /> Generating suggestions...</div>}
                
                {!loadingFixes && suggestedFixes.length > 0 && (
                  <div>
                    <div style={{ marginBottom: 4, fontWeight: 500, fontSize: 13 }}>Suggested Fixes</div>
                    <Space wrap>
                      {suggestedFixes.map(fix => (
                        <Button 
                          key={fix} 
                          size="small" 
                          onClick={() => setFeedback(fix)}
                        >
                          {fix}
                        </Button>
                      ))}
                    </Space>
                  </div>
                )}

                <Input.TextArea
                  rows={3}
                  placeholder="Provide feedback if rejecting, or optional notes for approval..."
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                />
                
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12 }}>
                  <Button
                    danger
                    icon={<XCircle size={16} />}
                    onClick={handleReject}
                    disabled={!feedback && !rejectionCategory || isResuming}
                    loading={isResuming}
                    title={(!feedback && !rejectionCategory) ? 'Feedback or Category is required to reject' : undefined}
                  >
                    Reject
                  </Button>
                  <Button
                    type="primary"
                    icon={<CheckCircle size={16} />}
                    onClick={handleApprove}
                    disabled={isResuming}
                    loading={isResuming}
                  >
                    Approve & Continue
                  </Button>
                </div>
              </div>
            </Card>
          )}

          {chatResponse.status === 'completed' && (
            <Card
              title={
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <CheckCircle size={18} color="#52c41a" /> Agent Completed
                </span>
              }
              extra={<Tag color="success">Done</Tag>}
              style={{
                border: '1px solid var(--border-color)',
                background: 'var(--bg-secondary)',
              }}
            >
              {chatResponse.summary && (
                <div style={{ marginBottom: 16 }}>
                  <h4 style={{ margin: '0 0 8px 0', color: 'var(--text-color)' }}>Summary</h4>
                  <div
                    className="markdown-body"
                    style={{ margin: 0, color: 'var(--text-muted)', fontSize: 14 }}
                  >
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {chatResponse.summary}
                    </ReactMarkdown>
                  </div>
                </div>
              )}

              {chatResponse.sql_query && (
                <div style={{ marginBottom: 16 }}>
                  <h4 style={{ margin: '0 0 8px 0', color: 'var(--text-color)' }}>
                    Final SQL Query
                  </h4>
                  <pre
                    style={{
                      background: 'rgba(0,0,0,0.3)',
                      padding: 16,
                      borderRadius: 8,
                      fontSize: 13,
                      overflowX: 'auto',
                      margin: 0,
                      color: '#61afef',
                      border: '1px solid rgba(255,255,255,0.05)',
                    }}
                  >
                    {chatResponse.sql_query}
                  </pre>
                  {chatResponse.sql_explanation && (
                    <div
                      className="markdown-body"
                      style={{ marginTop: 8, fontSize: 13, color: 'var(--text-muted)' }}
                    >
                      <strong>Explanation:</strong>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {chatResponse.sql_explanation}
                      </ReactMarkdown>
                    </div>
                  )}
                </div>
              )}

              {chatResponse.raw_data_ref && (
                <div style={{ marginTop: 20 }}>
                  <Tag color="processing">Raw Data Reference: {chatResponse.raw_data_ref}</Tag>
                </div>
              )}

              <Divider />
              <div style={{ display: 'flex', justifyContent: 'center', gap: 12 }}>
                <Button onClick={handleReset} icon={<Send size={14} />}>
                  Start New Request
                </Button>
                {threadId && (
                  <Button onClick={() => setTraceModalVisible(true)}>
                    View Full Trace
                  </Button>
                )}
              </div>
            </Card>
          )}
        </div>
      )}

      <Modal
        title="Execution Trace"
        open={traceModalVisible}
        onCancel={() => setTraceModalVisible(false)}
        footer={null}
        width={800}
      >
        {threadId && <TraceTimeline traceId={threadId} />}
      </Modal>
    </div>
  );
}
