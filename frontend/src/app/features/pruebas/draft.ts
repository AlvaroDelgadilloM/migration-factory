import { Injectable } from '@angular/core';
import { HttpCase } from '../../core/models';

export interface Target { label: string; url: string }

/** Per-browser conveniences of the Pruebas screens: the last systems used and the requests saved from "Manuales" to be
 *  added to an automatic run. Nothing here is the record of a test: runs and their evidence live in the platform. */
@Injectable({ providedIn: 'root' })
export class PruebasDraft {
  private read<T>(key: string, fallback: T): T {
    try { const v = localStorage.getItem(key); return v ? JSON.parse(v) as T : fallback; } catch { return fallback; }
  }
  private write(key: string, value: unknown) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: not kept */ }
  }

  targets(projectId: string): [Target, Target] {
    return this.read(`mf.pruebas.${projectId}.targets`, [{ label: 'JBoss (original)', url: '' }, { label: 'Tomcat (migrado)', url: '' }]);
  }
  saveTargets(projectId: string, t: [Target, Target]) { this.write(`mf.pruebas.${projectId}.targets`, t); }

  saved(projectId: string): HttpCase[] { return this.read(`mf.pruebas.${projectId}.cases`, []); }
  addCase(projectId: string, c: HttpCase) { this.write(`mf.pruebas.${projectId}.cases`, [...this.saved(projectId), c]); }
  clearCases(projectId: string) { this.write(`mf.pruebas.${projectId}.cases`, []); }
}
