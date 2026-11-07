import { describe, expect, it } from 'vitest';
import { humanEntity, humanProvider, humanRole, humanStatus } from './ui';

/** Los estados se leen en lenguaje humano; el código técnico solo acompaña en pequeño, nunca solo. */
describe('humanStatus', () => {
  it('traduce los códigos de corrida y de ejecución', () => {
    expect(humanStatus('queued')).toBe('En cola');
    expect(humanStatus('running')).toBe('En curso');
    expect(humanStatus('succeeded')).toBe('Correcto');
    expect(humanStatus('failed')).toBe('Fallido');
    expect(humanStatus('timed_out')).toBe('Tiempo agotado');
    expect(humanStatus('cancelled')).toBe('Cancelado');
    expect(humanStatus('SUCCEEDED')).toBe('Completado');
    expect(humanStatus('PARTIAL_SUCCESS')).toBe('Migrado con pendientes');
    expect(humanStatus('FAILED')).toBe('Falló');
  });

  it('traduce los códigos de validación y de DataStage', () => {
    expect(humanStatus('PASS')).toBe('Aprobado');
    expect(humanStatus('FAIL')).toBe('Fallido');
    expect(humanStatus('SKIPPED')).toBe('Omitido');
    expect(humanStatus('NOT_RUN')).toBe('No ejecutado');
    expect(humanStatus('AUTO_MIGRATED')).toBe('Automático');
    expect(humanStatus('MANUAL_REVIEW')).toBe('Trabajo manual');
    expect(humanStatus('VERIFIED')).toBe('Verificado');
    expect(humanStatus('EQUAL')).toBe('Igual');
    expect(humanStatus('DIFFERENT')).toBe('Distinto');
  });

  it('traduce los códigos de Odoo y de diff', () => {
    expect(humanStatus('AUTO')).toBe('Automático');
    expect(humanStatus('MANUAL')).toBe('Manual');
    expect(humanStatus('REVIEW')).toBe('Revisar');
    expect(humanStatus('ADDED')).toBe('Añadido');
    expect(humanStatus('MODIFIED')).toBe('Modificado');
    expect(humanStatus('DELETED')).toBe('Eliminado');
    expect(humanStatus('UNRESOLVED')).toBe('Sin resolver');
  });

  it('devuelve el código tal cual cuando no hay traducción, y — cuando no hay código', () => {
    expect(humanStatus('ALGO_NUEVO')).toBe('ALGO_NUEVO');
    expect(humanStatus(null)).toBe('—');
    expect(humanStatus('')).toBe('—');
  });
});

describe('humanRole / humanEntity / humanProvider', () => {
  it('traduce roles, entidades y proveedores', () => {
    expect(humanRole('owner')).toBe('Responsable');
    expect(humanRole('architect')).toBe('Arquitecto');
    expect(humanRole('developer')).toBe('Desarrollador');
    expect(humanRole('auditor')).toBe('Auditor');
    expect(humanEntity('finding')).toBe('Hallazgo');
    expect(humanEntity('execution')).toBe('Ejecución');
    expect(humanEntity('scan')).toBe('Análisis');
    expect(humanProvider('github')).toBe('GitHub');
    expect(humanProvider('gitlab')).toBe('GitLab');
    expect(humanProvider('bitbucket')).toBe('Bitbucket');
  });

  it('devuelve el valor tal cual cuando no hay traducción', () => {
    expect(humanRole('superuser')).toBe('superuser');
    expect(humanEntity('cosa')).toBe('cosa');
    expect(humanProvider('gitea')).toBe('gitea');
  });
});
