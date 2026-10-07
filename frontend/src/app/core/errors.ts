import { HttpErrorResponse } from '@angular/common/http';
import { ApiErrorBody } from './models';

/** Normalized error shown by the UI: always carries a requestId when the API produced one. */
export class UiError {
  constructor(public status: number, public code: string, public message: string,
              public requestId: string | null = null, public details: Record<string, unknown> = {}) {}

  get forbidden() { return this.status === 403; }
  get conflict() { return this.status === 409; }

  static from(e: unknown): UiError {
    if (e instanceof UiError) return e;
    if (e instanceof HttpErrorResponse) {
      const b = (e.error ?? {}) as Partial<ApiErrorBody>;
      if (e.status === 0) return new UiError(0, 'NETWORK', 'No se pudo contactar con la API');
      return new UiError(e.status, b.code ?? 'HTTP_' + e.status, b.message ?? e.message, b.requestId ?? null, b.details ?? {});
    }
    return new UiError(-1, 'CLIENT', String(e));
  }
}
