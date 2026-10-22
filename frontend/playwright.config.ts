import { defineConfig } from '@playwright/test';

/** End-to-end UI tests against a running stack (API with demo login + migrated data). Not part of `npm test`.
 *  E2E_BASE_URL defaults to the local Compose UI. */
export default defineConfig({
  testDir: 'e2e',
  globalSetup: './e2e/seed.ts',
  timeout: 30_000,
  retries: 0,
  use: { baseURL: process.env['E2E_BASE_URL'] ?? 'http://127.0.0.1:3240', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { browserName: 'chromium', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
