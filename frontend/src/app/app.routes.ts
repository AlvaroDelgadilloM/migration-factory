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
      { path: 'p/:projectId/odoo', title: 'Resumen · Odoo', loadComponent: () => import('./features/odoo/overview').then(m => m.OdooOverview) },
      { path: 'p/:projectId/odoo/pruebas', title: 'Pruebas · Odoo', loadComponent: () => import('./features/odoo/flows').then(m => m.OdooFlows) },
      { path: 'p/:projectId/odoo/codigo', title: 'Código · Odoo', loadComponent: () => import('./features/odoo/code').then(m => m.OdooCode) },
      { path: 'p/:projectId/odoo/modulos', title: 'Módulos · Odoo', loadComponent: () => import('./features/odoo/modules').then(m => m.OdooModules) },
      { path: 'p/:projectId/ds', title: 'Resumen · DataStage', loadComponent: () => import('./features/datastage/overview').then(m => m.DsOverview) },
      { path: 'p/:projectId/ds/jobs', title: 'Jobs · DataStage', loadComponent: () => import('./features/datastage/jobs').then(m => m.DsJobs) },
      { path: 'p/:projectId/ds/oleadas', title: 'Oleadas · DataStage', loadComponent: () => import('./features/datastage/waves').then(m => m.DsWaves) },
      { path: 'p/:projectId/ds/reconciliacion', title: 'Reconciliación · DataStage', loadComponent: () => import('./features/datastage/reconciliation').then(m => m.DsReconciliation) },
      { path: 'p/:projectId/ds/acciones', title: 'Acciones · DataStage', loadComponent: () => import('./features/datastage/actions').then(m => m.DsActions) },
      { path: 'p/:projectId/inventory', title: 'Inventario', loadComponent: () => import('./features/inventory/inventory').then(m => m.Inventory) },
      { path: 'p/:projectId/findings', title: 'Hallazgos', loadComponent: () => import('./features/findings/findings').then(m => m.Findings) },
      { path: 'p/:projectId/plan', title: 'Plan', loadComponent: () => import('./features/plan/plan').then(m => m.PlanPage) },
      { path: 'p/:projectId/execution', title: 'Ejecución', loadComponent: () => import('./features/execution/execution').then(m => m.ExecutionPage) },
      { path: 'p/:projectId/diff', title: 'Diff', loadComponent: () => import('./features/diff/diff').then(m => m.DiffPage) },
      { path: 'p/:projectId/validation', title: 'Validación', loadComponent: () => import('./features/validation/validation').then(m => m.ValidationPage) },
      { path: 'p/:projectId/pruebas/automaticas', title: 'Pruebas automáticas', loadComponent: () => import('./features/pruebas/automaticas').then(m => m.PruebasAutomaticas) },
      { path: 'p/:projectId/pruebas/manuales', title: 'Pruebas manuales', loadComponent: () => import('./features/pruebas/manuales').then(m => m.PruebasManuales) },
      { path: 'p/:projectId/settings', title: 'Proyecto', loadComponent: () => import('./features/settings/settings').then(m => m.Settings) },
      { path: 'rules', title: 'Reglas', loadComponent: () => import('./features/rules/rules').then(m => m.Rules) },
      { path: 'audit', title: 'Auditoría', loadComponent: () => import('./features/audit/audit').then(m => m.Audit) },
    ],
  },
  { path: '**', redirectTo: '' },
];
