import { computed, inject, Injectable, signal } from '@angular/core';
import { Api } from './api';
import { Permissions, Project } from './models';

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
    this.project.set(null);
    this.perms.set(null);
    if (!id) return;
    try {
      const [p, perms] = await Promise.all([this.api.project(id), this.api.permissions(id)]);
      if (this.projectId() === id) { this.project.set(p); this.perms.set(perms); }
    } catch { /* the page shows its own error state */ }
  }

  async refresh() { const id = this.projectId(); this.projectId.set(null); await this.select(id); }

  can(p: string) { return !!this.perms()?.permissions.includes(p); }
}
