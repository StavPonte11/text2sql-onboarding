import { useEffect, useState } from 'react';
import {
  ReactFlow,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

interface AgentGraphProps {
  threadId: string | null;
}

const formatLabel = (str: string) => {
  return str.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
};

const getStyle = (isCompleted: boolean, isActive: boolean) => {
  let border = '1px solid #d9d9d9';
  let background = '#fff';
  let boxShadow = 'none';

  if (isActive) {
    border = '2px solid #1677ff'; // blue
    background = '#e6f4ff';
    boxShadow = '0 0 10px rgba(22, 119, 255, 0.4)';
  } else if (isCompleted) {
    border = '2px solid #52c41a'; // green
    background = '#f6ffed';
  }

  return {
    border,
    background,
    color: '#000',
    boxShadow,
    borderRadius: '6px',
    padding: '10px',
    fontSize: '12px',
    transition: 'all 0.3s ease',
    width: 140,
    textAlign: 'center' as const,
  };
};

export function AgentGraph({ threadId }: AgentGraphProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [path, setPath] = useState<string[]>([]);

  useEffect(() => {
    if (!threadId) {
      setPath([]);
      return;
    }

    const eventSource = new EventSource(`/api/agent/stream/${threadId}`);
    
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.node) {
          setPath((prev) => [...prev, data.node]);
        }
      } catch (err) {
        console.error('Failed to parse SSE', err);
      }
    };

    eventSource.onerror = (err) => {
      console.log('SSE Error or connection closed', err);
    };

    return () => {
      eventSource.close();
    };
  }, [threadId]);

  useEffect(() => {
    // Dynamically build the graph based on the executed path
    const newNodes: any[] = [
      {
        id: 'START',
        position: { x: 50, y: 150 },
        data: { label: 'START' },
        type: 'input',
        style: getStyle(true, false),
      },
    ];
    const newEdges: any[] = [];

    path.forEach((step, i) => {
      const id = `${step}-${i}`;
      const isActive = i === path.length - 1;
      const isCompleted = i < path.length - 1;

      newNodes.push({
        id,
        position: { x: 50 + (i + 1) * 180, y: 150 },
        data: { label: formatLabel(step) },
        style: getStyle(isCompleted, isActive),
      });

      const sourceId = i === 0 ? 'START' : `${path[i - 1]}-${i - 1}`;
      newEdges.push({
        id: `e-${sourceId}-${id}`,
        source: sourceId,
        target: id,
        animated: isActive,
        markerEnd: { type: MarkerType.ArrowClosed },
      });
    });

    setNodes(newNodes);
    setEdges(newEdges);
  }, [path, setNodes, setEdges]);

  return (
    <div
      style={{
        width: '100%',
        height: '250px',
        border: '1px solid var(--border-color)',
        borderRadius: '8px',
        background: 'var(--bg-secondary)',
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        attributionPosition="bottom-right"
      >
        <Background gap={16} />
        <Controls />
      </ReactFlow>
    </div>
  );
}
