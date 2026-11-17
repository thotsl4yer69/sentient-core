import { defineConfig } from '@playwright/test';

const channel = process.env.AVATAR_BROWSER_CHANNEL || undefined;

export default defineConfig({
  testDir: './tests',
  testMatch: 'browser.spec.mjs',
  timeout: 180000,
  expect: { timeout: 20000 },
  use: {
    headless: true,
    viewport: { width: 640, height: 480 },
    launchOptions: {
      channel,
      args: [
        '--use-gl=angle',
        '--use-angle=swiftshader',
        '--enable-unsafe-swiftshader',
        '--autoplay-policy=no-user-gesture-required',
        '--disable-gpu-sandbox',
        '--no-sandbox',
        '--disable-dev-shm-usage',
      ],
    },
  },
  webServer: {
    command: 'python3 -m http.server 8765 --bind 127.0.0.1',
    cwd: '.',
    port: 8765,
    reuseExistingServer: !process.env.CI,
  },
});
