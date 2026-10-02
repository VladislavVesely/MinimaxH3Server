const { defineConfig } = require('@playwright/test');
const os = require('node:os');
const path = require('node:path');

module.exports = defineConfig({
  testDir: '.',
  testMatch: ['setup.spec.js', 'generation.spec.js', 'characters.spec.js', 'persistent-access.spec.js', 'memory-settings.spec.js', 'memory-settings-events.spec.js', 'job-favorites.spec.js'],
  outputDir: path.join(os.tmpdir(), 'h3-setup-browser-results'),
  fullyParallel: false,
  workers: 1,
  use: { browserName: 'chromium', headless: true, trace: 'off' },
  reporter: 'list',
});
