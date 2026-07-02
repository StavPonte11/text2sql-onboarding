import { useEffect } from 'react';
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react';

import '@xyflow/react/dist/style.css';

interface AgentGraphProps {
  threadId: string | null;
  executionPath?: string[];
  onNodeClick?: (nodeName: string, index: number) => void;
}

const formatLabel = (str: string) => {
  return str
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
};

const getStartNodeStyle = () => {
  return {
    border: '1px solid rgba(255, 255, 255, 0.15)',
    background: 'rgba(255, 255, 255, 0.04)',
    color: '#94A3B8',
    borderRadius: '8px',
    padding: '12px',
    fontSize: '12px',
    fontWeight: 600,
    width: 140,
    textAlign: 'center' as const,
    fontFamily: 'Fira Sans, system-ui, sans-serif',
    backdropFilter: 'blur(8px)',
  };
};

const getStyle = (isCompleted: boolean, isActive: boolean) => {
  let border = '1px solid rgba(255, 255, 255, 0.08)';
  let background = 'rgba(15, 23, 42, 0.5)';
  let color = '#64748B';
  let boxShadow = 'none';

  if (isActive) {
    border = '1px solid #38BDF8';
    background = 'rgba(56, 189, 248, 0.1)';
    color = '#38BDF8';
    boxShadow = '0 0 12px rgba(56, 189, 248, 0.25)';
  } else if (isCompleted) {
    border = '1px solid #10B981';
    background = 'rgba(16, 185, 129, 0.06)';
    color = '#34D399';
  }

  return {
    border,
    background,
    color,
    boxShadow,
    borderRadius: '8px',
    padding: '12px',
    fontSize: '12px',
    fontWeight: 500,
    transition: 'all 0.3s ease',
    width: 140,
    textAlign: 'center' as const,
    fontFamily: 'Fira Sans, system-ui, sans-serif',
    backdropFilter: 'blur(8px)',
    cursor: 'pointer',
  };
};

export function AgentGraph({ executionPath = [], onNodeClick }: AgentGraphProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    // Dynamically build the graph based on the executed path
    const newNodes: any[] = [
      {
        id: 'START',
        position: { x: 50, y: 100 },
        data: { label: 'START' },
        type: 'input',
        style: getStartNodeStyle(),
        sourcePosition: Position.Right,
      },
    ];
    const newEdges: any[] = [];

    executionPath.forEach((step, i) => {
      const id = `${step}-${i}`;
      const isActive = i === executionPath.length - 1;
      const isCompleted = i < executionPath.length - 1;

      newNodes.push({
        id,
        position: { x: 50 + (i + 1) * 180, y: 100 },
        data: { label: formatLabel(step) },
        style: getStyle(isCompleted, isActive),
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      });

      const sourceId = i === 0 ? 'START' : `${executionPath[i - 1]}-${i - 1}`;
      newEdges.push({
        id: `e-${sourceId}-${id}`,
        source: sourceId,
        target: id,
        animated: isActive,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: isActive ? '#38BDF8' : '#10B981',
        },
        style: {
          stroke: isActive ? '#38BDF8' : '#10B981',
          strokeWidth: 2,
        },
      });
    });

    setNodes(newNodes);
    setEdges(newEdges);
  }, [executionPath, setNodes, setEdges]);

  return (
    <div
      style={{
        width: '100%',
        height: '200px',
        border: '1px solid rgba(255, 255, 255, 0.08)',
        borderRadius: '12px',
        background: '#0B0F19',
        boxShadow: 'inset 0 2px 8px rgba(0, 0, 0, 0.3)',
        overflow: 'hidden',
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={(_, node) => {
          if (node.id === 'START') return;
          const [stepName, indexStr] = node.id.split('-');
          const index = parseInt(indexStr, 10);
          onNodeClick?.(stepName, index);
        }}
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
