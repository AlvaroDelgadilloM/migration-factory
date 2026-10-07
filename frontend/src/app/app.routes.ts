import { Routes } from '@angular/router';
import { authGuard } from './core/auth';
import { Login } from './features/login/login';
import { Shell } from './layout/shell';

export const routes: Routes = [
  { path: 'login', component: Login, title: 'Ingreso · Migration Factory' },
  {
    path: '', component: Shell, canActivate: [authGuard], children: [
      { path: '', pathMatch: 'full', redirectTo: 'portfolio' },
      { path: 'portfolio', title: 'Cartera', loadComponent: () => import('./features/portfolio/portfolio').then(m => m.Portfolio) },
      { path: 'p/:projectId/inventory', title: 'Inventario', loadComponent: () => import('./features/inventory/inventory').then(m => m.Inventory) },
      { path: 'p/:projectId/findings', title: 'Hallazgos', loadComponent: () => import('./features/findings/findings').then(m => m.Findings) },
      { path: 'p/:projectId/plan', title: 'Plan', loadComponent: () => import('./features/plan/plan').then(m => m.PlanPage) },
      { path: 'p/:projectId/execution', title: 'Ejecución', loadComponent: () => import('./features/execution/execution').then(m => m.ExecutionPage) },
      { path: 'p/:projectId/diff', title: 'Diff', loadComponent: () => import('./features/diff/diff').then(m => m.DiffPage) },
      { path: 'p/:projectId/validation', title: 'Validación', loadComponent: () => import('./features/validation/validation').then(m => m.ValidationPage) },
      { path: 'p/:projectId/settings', title: 'Proyecto', loadComponent: () => import('./features/settings/settings').then(m => m.Settings) },
      { path: 'rules', title: 'Reglas', loadComponent: () => import('./features/rules/rules').then(m => m.Rules) },
      { path: 'audit', title: 'Auditoría', loadComponent: () => import('./features/audit/audit').then(m => m.Audit) },
    ],
  },
  { path: '**', redirectTo: '' },
];
