import { describe, expect, it } from 'vitest';

import { findBirdConnection } from '../utils/birdConnection';

const conn = (connection_id: number, name: string) => ({ connection_id, name });

describe('findBirdConnection', () => {
  it('picks the connection named bird, wherever it is in the list', () => {
    const list = [conn(6, 'AdventureWorksDW'), conn(9, 'bird_books'), conn(1166, 'BIRD ')];
    expect(findBirdConnection(list)?.connection_id).toBe(1166);
  });

  it('falls back to a connection whose name starts with bird', () => {
    expect(
      findBirdConnection([conn(6, 'AdventureWorksDW'), conn(9, 'bird_books')])?.connection_id,
    ).toBe(9);
  });

  it('finds nothing when there is no BIRD connection', () => {
    expect(findBirdConnection([conn(6, 'AdventureWorksDW'), conn(7, 'blackbird')])).toBeUndefined();
  });
});
