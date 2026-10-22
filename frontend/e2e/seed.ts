import { request } from '@playwright/test';
import { readFileSync } from 'node:fs';

/** Global setup: the UI tests need one analysed Java project and one migrated DataStage project. On a fresh stack (CI) they
 *  are created through the API from synthetic sources: /sources/pilot-orders and the DSX ZIP in E2E_DSX_ZIP. */
export default async function seed() {
  const base = process.env['E2E_BASE_URL'] ?? 'http://127.0.0.1:3240';
  const api = await request.newContext({ baseURL: base });
  const tok = (await (await api.post('/api/v1/auth/dev-login', { data: { username: 'ana.arquitecta' } })).json()).accessToken;
  const h = { Authorization: `Bearer ${tok}` };
  const projects = (await (await api.get('/api/v1/projects?limit=200', { headers: h })).json()).items as any[];
  const wait = async (url: string, done: (x: any) => boolean) => {
    for (let i = 0; i < 400; i++) {
      const x = await (await api.get(url, { headers: h })).json();
      if (done(x)) return x;
      await new Promise(r => setTimeout(r, 3000));
    }
    throw new Error(`seed: tiempo agotado en ${url}`);
  };
  if (!projects.some(p => p.migrationType === 'JAVA_CAMEL' && p.lastScan?.status === 'succeeded')) {
    const p = await (await api.post('/api/v1/projects', { headers: h,
      data: { name: `E2E seed ${Date.now()}`, sourceType: 'local', localPath: '/sources/pilot-orders', targetRuntime: 'spring' } })).json();
    const s = await (await api.post(`/api/v1/projects/${p.id}/scans`, { headers: h, data: {} })).json();
    await wait(`/api/v1/scans/${s.scanId}`, x => ['succeeded', 'failed'].includes(x.status));
  }
  const zip = process.env['E2E_DSX_ZIP'];
  if (zip && !projects.some(p => p.migrationType === 'DATASTAGE_AIRFLOW')) {
    const up = await (await api.post('/api/v1/uploads', { headers: { ...h, 'Content-Type': 'application/zip', 'X-Filename': 'dsx.zip',
      'X-Migration-Type': 'DATASTAGE_AIRFLOW' }, data: readFileSync(zip) })).json();
    const p = await (await api.post('/api/v1/projects', { headers: h, data: { name: `E2E DataStage ${Date.now()}`, sourceType: 'zip',
      uploadId: up.id, migrationType: 'DATASTAGE_AIRFLOW', targetRuntime: 'airflow' } })).json();
    const m = await (await api.post(`/api/v1/projects/${p.id}/datastage-migrations`, { headers: h, data: {} })).json();
    await wait(`/api/v1/datastage-migrations/${m.dataStageMigrationId}`, x => !['queued', 'running'].includes(x.state));
  }
  await api.dispose();
}
