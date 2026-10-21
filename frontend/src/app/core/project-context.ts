import { computed, inject, Injectable, signal, untracked } from '@angular/core';
import { Api } from './api';
import { Permissions, Project } from './models';

export type DestinationRuntime = 'tomcat' | 'spring' | 'quarkus';
export interface Destination { runtime: DestinationRuntime; java: string; spring: string | null }
const DEFAULT_DESTINATION: Destination = { runtime: 'spring', java: '21', spring: null };

/** Currently selected project and the caller's permissions on it (drives enabled/disabled actions). */
@Injectable({ providedIn: 'root' })
export class ProjectContext {
  private api = inject(Api);
  readonly projectId = signal<string | null>(null);
  readonly project = signal<Project | null>(null);
  readonly perms = signal<Permissions | null>(null);
  readonly role = computed(() => this.perms()?.isAdmin ? 'admin' : this.perms()?.role ?? null);

  async select(id: string | null) {
    if (id === this.projectId() && this.project()) return;
    this.projectId.set(id);
    this.loadDestination(id);
    this.project.set(null);
    this.perms.set(null);
    if (!id) return;
    try {
      const [p, perms] = await Promise.all([this.api.project(id), this.api.permissions(id)]);
      if (this.projectId() === id) { this.project.set(p); this.perms.set(perms); }
    } catch { /* the page shows its own error state */ }
  }

  async refresh() { const id = this.projectId(); this.projectId.set(null); await this.select(id); }

  /** Destination, Java and Spring chosen in Inventario for this project: the default of Migración por oleadas and Plan.
   *  Remembered per project in this browser (a convenience: the version actually used is the one sent with each request). */
  readonly destination = signal<Destination>(DEFAULT_DESTINATION);
  /** false until someone chooses: then the page may suggest one (the project's runtime, or Tomcat WAR for a Fuse workspace) */
  readonly destinationChosen = signal(false);

  loadDestination(id: string | null) {
    let saved: Partial<Destination> | null = null;
    try { saved = id ? JSON.parse(localStorage.getItem('mf.destination.' + id) ?? 'null') : null; } catch { saved = null; }
    this.destination.set({ ...DEFAULT_DESTINATION, ...(saved ?? {}) });
    this.destinationChosen.set(!!saved);
  }

  /** A default that does not count as a choice (nothing is remembered). */
  suggestDestination(runtime: DestinationRuntime) {
    untracked(() => {  // never a dependency of the caller's effect: it writes the signal it would read
      if (!this.destinationChosen() && this.destination().runtime !== runtime) this.destination.update(d => ({ ...d, runtime, spring: null }));
    });
  }

  setDestination(d: Partial<Destination>) {
    this.destination.update(cur => ({ ...cur, ...d }));
    this.destinationChosen.set(true);
    const id = this.projectId();
    try { if (id) localStorage.setItem('mf.destination.' + id, JSON.stringify(this.destination())); } catch { /* not remembered */ }
  }

  can(p: string) { return !!this.perms()?.permissions.includes(p); }
}
