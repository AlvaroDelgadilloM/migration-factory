import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Portfolio } from './portfolio';

describe('Portfolio · registro de proyecto por origen', () => {
  let http: HttpTestingController;
  let c: Portfolio;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] });
    c = TestBed.runInInjectionContext(() => new Portfolio());
    http = TestBed.inject(HttpTestingController);
    http.expectOne(r => r.url === '/api/v1/projects').flush({ items: [], total: 0, nextCursor: null });
    c.openNew();
    http.expectOne('/api/v1/capabilities').flush({ localSourceEnabled: true, localSourceRoots: ['/sources'], maxUploadMb: 200, gitAllowedHosts: ['github.com'] });
  });

  it('exige elegir un origen', () => {
    expect(c.sourceReady()).toBe(false);
  });

  it('carpeta local: solo rutas absolutas dentro de directorios autorizados, sin Git', () => {
    c.form.sourceType = 'local';
    c.form.localPath = '/etc/passwd';
    expect(c.sourceReady()).toBe(false);
    c.form.localPath = '/sources/../etc';
    expect(c.sourceReady()).toBe(false);
    c.form.localPath = '/sources/pilot-orders';
    expect(c.sourceReady()).toBe(true);
  });

  it('git: solo https, nunca file://', () => {
    c.form.sourceType = 'git';
    c.form.repositoryUrl = 'file:///sources/pilot-orders';
    expect(c.sourceReady()).toBe(false);
    c.form.repositoryUrl = 'https://github.com/acme/orders.git';
    expect(c.sourceReady()).toBe(true);
  });

  it('zip: requiere un archivo subido y envía solo uploadId', async () => {
    c.form.sourceType = 'zip';
    expect(c.sourceReady()).toBe(false);
    c.uploaded.set({ id: 'u1', filename: 'p.zip', sha256: 'x'.repeat(64), sizeBytes: 10, structure: { root: '.', modules: ['pom.xml'] }, createdAt: '' });
    expect(c.sourceReady()).toBe(true);
    c.form.name = 'Piloto';
    c.form.repositoryUrl = 'https://github.com/x/y.git';  // ignored for zip
    const done = c.create();
    const req = http.expectOne('/api/v1/projects');
    expect(req.request.body).toEqual({ name: 'Piloto', sourceType: 'zip', targetRuntime: 'spring', description: '', uploadId: 'u1' });
    req.flush({ message: 'x', code: 'X' }, { status: 422, statusText: 'x' });
    await done;
  });

  it('carpeta local deshabilitada en instalación remota, con explicación', () => {
    c.caps.set({ localSourceEnabled: false, localSourceRoots: [], maxUploadMb: 200, gitAllowedHosts: [] });
    const local = c.sourceOptions().find(o => o.value === 'local')!;
    expect(local.disabled).toBe(true);
    expect(local.hint).toContain('agente local');
  });
});
