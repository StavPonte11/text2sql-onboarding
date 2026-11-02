import React, { useState } from 'react';
import { CheckOutlined, CopyOutlined } from '@ant-design/icons';

interface JsonTreeViewProps {
  label: 'INPUT' | 'OUTPUT';
  content: string;
  themeColor: string;
}

// Recursive component to render JSON as a collapsible tree
const RenderNode: React.FC<{ data: any; depth?: number }> = ({ data, depth = 0 }) => {
  const isObject = typeof data === 'object' && data !== null;
  if (!isObject) {
    return <span>{String(data)}</span>;
  }
  const entries = Array.isArray(data) ? data.map((v, i) => [i, v]) : Object.entries(data);
  return (
    <ul style={{ listStyle: 'none', margin: 0, paddingLeft: depth * 12 }}>
      {entries.map(([key, value]) => (
        <li key={key} style={{ marginBottom: 4 }}>
          <details open={depth < 1} style={{ cursor: 'pointer' }}>
            <summary style={{ fontWeight: depth === 0 ? '600' : '400' }}>
              {Array.isArray(data) ? `[${key}]` : key}:
            </summary>
            <RenderNode data={value} depth={depth + 1} />
          </details>
        </li>
      ))}
    </ul>
  );
};

export default function JsonTreeView({ label, content, themeColor }: JsonTreeViewProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const trimmed = content?.trim() || '';
  const isJson =
    (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'));

  let renderedContent: React.ReactNode;
  if (isJson) {
    try {
      const parsed = JSON.parse(trimmed);
      renderedContent = <RenderNode data={parsed} />;
    } catch (e) {
      renderedContent = <span>{content}</span>;
    }
  } else {
    renderedContent = <span>{content}</span>;
  }

  return (
    <div style={{ position: 'relative' }} className="json-block-container">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 6,
        }}
      >
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '1px', color: themeColor }}>
          {label}
        </span>
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
      <div
        style={{
          background: '#0B0F19',
          border: '1px solid #1E293B',
          borderLeft: `3px solid ${themeColor}`,
          padding: '12px 16px',
          borderRadius: '6px',
          fontSize: '11px',
          maxHeight: '200px',
          overflowY: 'auto',
          boxShadow: 'inset 0 2px 4px rgba(0, 0, 0, 0.2)',
          position: 'relative',
        }}
      >
        {renderedContent}
      </div>
    </div>
  );
}
