import { render } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

// Capture what the graph hands to React Flow instead of drawing it
const drawn: { nodes: any[]; edges: any[] } = { nodes: [], edges: [] };
vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return {
    ...actual,
    ReactFlow: ({ nodes, edges }: { nodes: any[]; edges: any[] }) => {
      drawn.nodes = nodes;
      drawn.edges = edges;
      return null;
    },
    Background: () => null,
    Controls: () => null,
  };
});

import { AgentGraph } from '../components/AgentGraph';

const ACTIVE = '#38BDF8';
const path = ['deep:search_column_values', 'deep:execute_trino_query', 'deep:answer'];

const steps = () => drawn.nodes.filter((n) => n.id !== 'START');

describe('AgentGraph', () => {
  test('the last step is active while the run is in progress', () => {
    render(<AgentGraph threadId="t" executionPath={path} running />);
    expect(steps().map((n) => n.style.color === ACTIVE)).toEqual([false, false, true]);
    expect(drawn.edges.map((e) => e.animated)).toEqual([false, false, true]);
  });

  test('every step is completed once the run has finished', () => {
    render(<AgentGraph threadId="t" executionPath={path} running={false} />);
    expect(steps().some((n) => n.style.color === ACTIVE)).toBe(false);
    expect(drawn.edges.some((e) => e.animated)).toBe(false);
  });
});
