import { useState, useEffect, ReactNode } from 'react';
import { Timeline, Spin, Tag, Typography, Collapse, Empty } from 'antd';
import { CaretRightOutlined, ClockCircleOutlined, CopyOutlined, CheckOutlined } from '@ant-design/icons';
import axios from 'axios';
import JsonTreeView from '../components/JsonTreeView';

const { Text } = Typography;
const { Panel } = Collapse;

interface TraceSpan {
  span_name: string;
  start_time: string;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  model: string;
  status: string;
  input_preview: string;
  output_preview: string;
}

interface TraceTimelineProps {
  traceId: string;
}
export function highlightJson(json: string): string {
  if (!json) return '';
  const entityMap: { [key: string]: string } = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
    '/': '&#x2F;'
  };
  const escapeHtml = (text: string) => text.replace(/[&<>"'/]/g, m => entityMap[m]);

  const jsonRegex = /("(?:[^"\\]|\\.)*"(?:\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g;
  
  let lastIndex = 0;
  let html = '';
  let match;
  
  while ((match = jsonRegex.exec(json)) !== null) {
    html += escapeHtml(json.substring(lastIndex, match.index));
    
    const token = match[0];
    let cls = 'json-number';
    
    if (token.startsWith('"')) {
      if (token.endsWith(':')) {
        cls = 'json-key';
      } else {
        cls = 'json-string';
      }
    } else if (token === 'true' || token === 'false') {
      cls = 'json-boolean';
    } else if (token === 'null') {
      cls = 'json-null';
    }
    
    if (cls === 'json-key') {
      const lastQuoteIndex = token.lastIndexOf('"');
      const keyPart = token.substring(0, lastQuoteIndex + 1);
      const colonPart = token.substring(lastQuoteIndex + 1);
      html += `<span class="${cls}">${escapeHtml(keyPart)}</span>${escapeHtml(colonPart)}`;
    } else {
      html += `<span class="${cls}">${escapeHtml(token)}</span>`;
    }
    
    lastIndex = jsonRegex.lastIndex;
  }
  
  html += escapeHtml(json.substring(lastIndex));
  return html;
}

interface FormattedBlockProps {
  label: 'INPUT' | 'OUTPUT';
  content: string;
  themeColor: string;
}

export function FormattedBlock({ label, content, themeColor }: FormattedBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const trimmed = content?.trim() || '';
  const isJson = (trimmed.startsWith('{') && trimmed.endsWith('}')) || 
                 (trimmed.startsWith('[') && trimmed.endsWith(']'));

  let highlightedElement: ReactNode;
  if (isJson) {
    try {
      const parsed = JSON.parse(trimmed);
      const formatted = JSON.stringify(parsed, null, 2);
      const highlighted = highlightJson(formatted);
      highlightedElement = <span dangerouslySetInnerHTML={{ __html: highlighted }} />;
    } catch (e) {
      highlightedElement = <span>{content}</span>;
    }
  } else {
    highlightedElement = <span>{content}</span>;
  }

  return (
    <div style={{ position: 'relative' }} className="json-block-container">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '1px', color: themeColor }}>{label}</span>
        <button
          onClick={handleCopy}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            fontSize: '12px',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            padding: '2px 6px',
            borderRadius: '4px',
            transition: 'all 0.2s',
          }}
          className="copy-btn"
          title="Copy to clipboard"
        >
          {copied ? (
            <>
              <CheckOutlined style={{ color: '#10B981' }} />
              <span style={{ fontSize: '10px', color: '#10B981' }}>Copied!</span>
            </>
          ) : (
            <>
              <CopyOutlined />
              <span style={{ fontSize: '10px' }}>Copy</span>
            </>
          )}
        </button>
      </div>
      <div style={{ 
        background: '#0B0F19', 
        border: '1px solid #1E293B',
        borderLeft: `3px solid ${themeColor}`,
        padding: '12px 16px', 
        borderRadius: '6px', 
        fontSize: '11px', 
        maxHeight: '200px', 
        overflowY: 'auto',
        boxShadow: 'inset 0 2px 4px rgba(0, 0, 0, 0.2)',
        position: 'relative'
      }}>
        <pre style={{ 
          margin: 0, 
          whiteSpace: 'pre-wrap', 
          fontFamily: 'var(--mono), monospace', 
          color: '#E2E8F0',
          lineHeight: '1.5'
        }}>
          {highlightedElement}
        </pre>
      </div>
    </div>
  );
}

const jsonHighlightStyles = `
  .json-block-container {
    position: relative;
  }
  .copy-btn {
    opacity: 0.6;
  }
  .copy-btn:hover {
    opacity: 1;
    background: rgba(255, 255, 255, 0.05) !important;
    color: var(--text-h) !important;
  }
  .json-key { color: #818CF8; font-weight: 600; }
  .json-string { color: #34D399; }
  .json-number { color: #FB923C; }
  .json-boolean { color: #F472B6; font-weight: 500; }
  .json-null { color: #64748B; font-style: italic; }
`;

export function TraceTimeline({ traceId }: TraceTimelineProps) {
  const [spans, setSpans] = useState<TraceSpan[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!traceId) return;

    setLoading(true);
    axios.get(`/api/agent/traces/${traceId}`)
      .then(res => {
        setSpans(res.data || []);
      })
      .catch(err => {
        console.error("Failed to load trace", err);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [traceId]);

  if (loading) {
    return <div style={{ padding: 20, textAlign: 'center' }}><Spin /></div>;
  }

  if (spans.length === 0) {
    return <Empty description="No trace data available yet." />;
  }

  return (
    <div style={{ padding: '16px 0' }}>
      <style dangerouslySetInnerHTML={{ __html: jsonHighlightStyles }} />
      <Timeline>
        {spans.map((span, idx) => (
          <Timeline.Item 
            key={idx} 
            color={span.status === 'error' ? 'red' : 'blue'}
            dot={span.span_name.includes('llm') || span.model !== 'N/A' ? <ClockCircleOutlined style={{ fontSize: '16px' }} /> : undefined}
          >
            <div style={{ marginBottom: 4 }}>
              <Text strong style={{ color: 'var(--text-h)' }}>{span.span_name}</Text>
              <span style={{ marginLeft: 8, color: 'var(--text-muted)', fontSize: 12 }}>
                {span.duration_ms}ms
              </span>
              {span.model !== 'N/A' && (
                <Tag color="purple" style={{ marginLeft: 8 }}>{span.model}</Tag>
              )}
              {span.input_tokens > 0 && (
                <Tag color="cyan">In: {span.input_tokens}</Tag>
              )}
              {span.output_tokens > 0 && (
                <Tag color="geekblue">Out: {span.output_tokens}</Tag>
              )}
            </div>

            {(span.input_preview || span.output_preview) && (
              <Collapse 
                ghost 
                size="small" 
                expandIcon={({ isActive }) => <CaretRightOutlined rotate={isActive ? 90 : 0} style={{ color: 'var(--text-muted)' }} />}
              >
                <Panel header={<span style={{ color: 'var(--text-muted)', fontSize: 12 }}>View Details</span>} key="1">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 8 }}>
                    {span.input_preview && (
                      <JsonTreeView 
                        label="INPUT" 
                        content={span.input_preview} 
                        themeColor="#38BDF8" 
                      />
                    )}
                    {span.output_preview && (
                      <JsonTreeView 
                        label="OUTPUT" 
                        content={span.output_preview} 
                        themeColor="#10B981" 
                      />
                    )}
                  </div>
                </Panel>
              </Collapse>
            )}
          </Timeline.Item>
        ))}
      </Timeline>
    </div>
  );
}
