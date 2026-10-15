import { describe, expect, it, vi } from 'vitest';
import { uuid } from './api';

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('uuid (Idempotency-Key)', () => {
  it('works without crypto.randomUUID (plain HTTP, non-secure context)', () => {
    const real = globalThis.crypto;
    const original = real.randomUUID;
    vi.stubGlobal('crypto', { getRandomValues: (a: Uint8Array) => real.getRandomValues(a), randomUUID: undefined });
    try {
      const ids = new Set(Array.from({ length: 200 }, () => uuid()));
      expect(ids.size).toBe(200);
      for (const id of ids) expect(id).toMatch(V4);
    } finally {
      vi.unstubAllGlobals();
      expect(crypto.randomUUID).toBe(original);
    }
  });

  it('uses crypto.randomUUID when the context is secure', () => {
    expect(uuid()).toMatch(V4);
  });
});
