// The Jeen connection the public BIRD benchmark runs on: the one named "bird",
// else the first whose name starts with "bird" (e.g. "bird_movie_platform").
export const BIRD_CONNECTION_NAME = 'bird';

export function findBirdConnection<T extends { name?: string | null }>(
  connections: T[],
): T | undefined {
  const name = (c: T) => (c.name || '').trim().toLowerCase();
  return (
    connections.find((c) => name(c) === BIRD_CONNECTION_NAME) ??
    connections.find((c) => name(c).startsWith(BIRD_CONNECTION_NAME))
  );
}
