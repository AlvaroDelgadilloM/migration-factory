import { expect, Page, test } from '@playwright/test';

async function login(page: Page) {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Entrar como Ana (arquitecta)' }).click();
  await page.waitForURL('**/portfolio');
}

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test('login de demostración lleva a la cartera', async ({ page }) => {
  await login(page);
  await expect(page.getByRole('heading', { name: /Hola, Ana/ })).toBeVisible();
  await noHorizontalScroll(page);
});

test('proyecto Java muestra la barra de avance y su siguiente paso', async ({ page }) => {
  await login(page);
  await page.locator('a[href*="/inventory"]').first().click();
  const flow = page.getByRole('navigation', { name: 'Avance de la migración' });
  await expect(flow).toBeVisible();
  for (const step of ['Analizar', 'Hallazgos', 'Plan', 'Ejecución', 'Validación']) await expect(flow.getByText(step, { exact: true })).toBeVisible();
  await flow.getByRole('link', { name: 'Hallazgos' }).click();
  await expect(page).toHaveURL(/\/findings/);
  await expect(flow).toBeVisible();
  await noHorizontalScroll(page);
});

test('secuencia DataStage abre su diagrama frente al DAG de Airflow', async ({ page, isMobile }) => {
  test.skip(isMobile, 'el diagrama se revisa en escritorio');
  await login(page);
  await page.locator('a[href$="/ds"]').first().click();
  await page.waitForURL(/\/ds$/);
  await page.goto(page.url() + '/jobs');
  await page.getByText('Secuencias', { exact: true }).click();
  await page.locator('tr.clickable').first().click();
  await expect(page.getByRole('heading', { name: 'Secuencia de DataStage → DAG de Airflow' })).toBeVisible();
  await expect(page.getByRole('img', { name: /Secuencia de \d+ actividades/ })).toBeVisible();
});

test('en celular el menú del proyecto es una fila horizontal y ninguna pantalla se desborda', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'solo celular');
  await login(page);
  const java = (await page.locator('a[href*="/inventory"]').first().getAttribute('href'))!.replace(/\/inventory$/, '');
  const ds = (await page.locator('a[href$="/ds"]').first().getAttribute('href'))!;
  await page.goto(ds);
  await expect(page.getByRole('link', { name: 'Jobs' })).toBeVisible();
  const screens = ['/portfolio', '/rules', '/audit', ...['', '/jobs', '/oleadas', '/acciones', '/reconciliacion'].map(x => ds + x),
                   ...['/inventory', '/findings', '/plan', '/execution', '/diff', '/validation', '/settings'].map(x => java + x)];
  for (const url of screens) {
    await page.goto(url);
    await page.waitForLoadState('networkidle');
    await test.step(url, () => noHorizontalScroll(page));
  }
});
