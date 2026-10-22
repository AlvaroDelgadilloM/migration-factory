import { APIRequestContext, expect, Page, test } from '@playwright/test';

/** Full Java/Camel flow against a running stack with real Maven in the worker (slow: E2E_FULL=1). The source is the synthetic
 *  pilot (examples/pilot-orders, mounted at /sources): analyze → resolve the blocking review → plan → preview → every screen. */
test.skip(!process.env['E2E_FULL'], 'flujo completo: E2E_FULL=1 (necesita el stack con worker y Maven)');
test.skip(({ isMobile }) => !!isMobile, 'el flujo completo corre una vez (escritorio)');
test.setTimeout(25 * 60_000);

const API = '/api/v1';

async function token(request: APIRequestContext) {
  const r = await request.post(`${API}/auth/dev-login`, { data: { username: 'ana.arquitecta' } });
  expect(r.ok()).toBeTruthy();
  return (await r.json()).accessToken as string;
}

async function waitFor<T>(fn: () => Promise<T | null>, what: string, ms = 20 * 60_000): Promise<T> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const v = await fn();
    if (v) return v;
    await new Promise(r => setTimeout(r, 3000));
  }
  throw new Error(`tiempo agotado esperando ${what}`);
}

async function login(page: Page) {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Entrar como Ana (arquitecta)' }).click();
  await page.waitForURL('**/portfolio');
}

test('flujo Java completo: análisis, plan, ejecución, diff y validación', async ({ page, request }) => {
  const h = { Authorization: `Bearer ${await token(request)}` };
  const name = `E2E pilot ${Date.now()}`;
  const p = await (await request.post(`${API}/projects`, { headers: h,
    data: { name, sourceType: 'local', localPath: '/sources/pilot-orders', targetRuntime: 'spring' } })).json();
  const scan = await (await request.post(`${API}/projects/${p.id}/scans`, { headers: h, data: {} })).json();
  const done = await waitFor(async () => {
    const s = await (await request.get(`${API}/scans/${scan.scanId}`, { headers: h })).json();
    if (['failed', 'cancelled', 'timed_out'].includes(s.status)) throw new Error(`análisis ${s.status}`);
    return s.status === 'succeeded' ? s : null;
  }, 'el análisis');

  // the architect accepts the Camel 2 review with a reason (blocking findings never pass silently)
  const findings = (await (await request.get(`${API}/scans/${done.id}/findings?limit=200`, { headers: h })).json()).items as any[];
  for (const f of findings.filter(x => x.blocking && x.ruleId === 'CAMEL2_VERSION')) {
    const etag = (await request.get(`${API}/findings/${f.id}`, { headers: h })).headers()['etag'];
    const r = await request.patch(`${API}/findings/${f.id}/review`, { headers: { ...h, 'If-Match': etag },
      data: { decision: 'resolved', reason: 'E2E: receta camel-2-to-3 revisada contra la guía' } });
    expect(r.ok()).toBeTruthy();
  }
  const profile = ((await (await request.get(`${API}/profiles`, { headers: h })).json()) as any[]).find(x => x.runtime === 'spring');
  const plan = await (await request.post(`${API}/scans/${done.id}/plans`, { headers: h, data: { profileId: profile.id } })).json();
  const finished = (id: string, what: string) => waitFor(async () => {
    const e = await (await request.get(`${API}/executions/${id}`, { headers: h })).json();
    return ['succeeded', 'failed', 'partial_success', 'blocked', 'cancelled', 'timed_out'].includes(e.state) ? e : null;
  }, what);
  const pre = await (await request.post(`${API}/plans/${plan.id}/preview`, { headers: h })).json();
  expect(pre.executionId, JSON.stringify(pre)).toBeTruthy();
  expect((await finished(pre.executionId, 'la previsualización')).state).toBe('succeeded');
  const etag = (await request.get(`${API}/plans/${plan.id}`, { headers: h })).headers()['etag'];
  expect((await request.post(`${API}/plans/${plan.id}/approve`, { headers: { ...h, 'If-Match': etag } })).ok()).toBeTruthy();
  const apply = await (await request.post(`${API}/plans/${plan.id}/executions`, { headers: h, data: { mode: 'apply' } })).json();
  expect(apply.executionId, JSON.stringify(apply)).toBeTruthy();
  const applied = await finished(apply.executionId, 'la ejecución aplicada');
  expect(['succeeded', 'partial_success'], JSON.stringify(applied.summary)).toContain(applied.state);

  await login(page);
  const base = `/p/${p.id}`;
  await page.goto(`${base}/inventory`);
  const flow = page.getByRole('navigation', { name: 'Avance de la migración' });
  await expect(flow).toBeVisible();
  await expect(page.getByText(/rutas?/i).first()).toBeVisible();
  await page.goto(`${base}/findings`);
  await expect(page.getByText('CAMEL2_VERSION').first()).toBeVisible();
  await page.goto(`${base}/plan`);
  await expect(page.getByText('camel-2-to-3').first()).toBeVisible();
  await page.goto(`${base}/execution`);
  await expect(page.getByText(/camel-3-to-4|runtime-spring/).first()).toBeVisible();
  await page.goto(`${base}/diff`);
  await expect(page.getByText(/pom\.xml|\.java/).first()).toBeVisible();
  await page.goto(`${base}/validation`);
  await expect(page.getByText('Sin ejecuciones aplicadas')).toHaveCount(0);
  await expect(page.getByText(/\bG[0-4]\b/).first()).toBeVisible();
});
