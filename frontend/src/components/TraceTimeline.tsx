import { useState, useEffect } from 'react';
import { Timeline, Spin, Tag, Typography, Button, Collapse, Empty } from 'antd';
import { CaretRightOutlined, ClockCircleOutlined } from '@ant-design/icons';
import axios from 'axios';

const { Text, Paragraph } = Typography;
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
      <Timeline>
        {spans.map((span, idx) => (
          <Timeline.Item 
            key={idx} 
            color={span.status === 'error' ? 'red' : 'blue'}
            dot={span.span_name.includes('llm') || span.model !== 'N/A' ? <ClockCircleOutlined style={{ fontSize: '16px' }} /> : undefined}
          >
            <div style={{ marginBottom: 4 }}>
              <Text strong>{span.span_name}</Text>
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
                expandIcon={({ isActive }) => <CaretRightOutlined rotate={isActive ? 90 : 0} />}
              >
                <Panel header="View I/O" key="1">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {span.input_preview && (
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>Input</Text>
                        <div style={{ background: 'var(--bg-secondary)', padding: 8, borderRadius: 6, fontSize: 12, maxHeight: 150, overflowY: 'auto' }}>
                          <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                            {span.input_preview.length > 2000 ? span.input_preview.substring(0, 2000) + '...' : span.input_preview}
                          </pre>
                        </div>
                      </div>
                    )}
                    {span.output_preview && (
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>Output</Text>
                        <div style={{ background: 'var(--bg-secondary)', padding: 8, borderRadius: 6, fontSize: 12, maxHeight: 150, overflowY: 'auto' }}>
                          <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                            {span.output_preview.length > 2000 ? span.output_preview.substring(0, 2000) + '...' : span.output_preview}
                          </pre>
                        </div>
                      </div>
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
