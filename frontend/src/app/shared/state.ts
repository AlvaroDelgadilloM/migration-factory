import { signal } from '@angular/core';
import { UiError } from '../core/errors';

/** Loading / error / data signals for one async source. Keeps the last good data on error. */
export class Loader<T> {
  readonly data = signal<T | null>(null);
  readonly loading = signal(false);
  readonly error = signal<UiError | null>(null);
  private last?: () => Promise<T>;

  async load(fn?: () => Promise<T>): Promise<T | null> {
    if (fn) this.last = fn;
    if (!this.last) return null;
    this.loading.set(true);
    this.error.set(null);
    try {
      const v = await this.last();
      this.data.set(v);
      return v;
    } catch (e) {
      this.error.set(UiError.from(e));
      return null;
    } finally {
      this.loading.set(false);
    }
  }

  reload() { return this.load(); }
}

/** Cursor pagination helper: keeps a stack of cursors so "previous" works with opaque server cursors. */
export class CursorPager {
  private stack: (string | null)[] = [null];
  readonly page = signal(0);
  current() { return this.stack[this.page()]; }
  next(cursor: string | null | undefined) { if (cursor) { this.stack = [...this.stack.slice(0, this.page() + 1), cursor]; this.page.update(p => p + 1); } }
  prev() { if (this.page() > 0) this.page.update(p => p - 1); }
  reset() { this.stack = [null]; this.page.set(0); }
}
