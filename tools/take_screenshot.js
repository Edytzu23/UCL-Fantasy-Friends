const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
  await page.goto('http://localhost:8000', { waitUntil: 'load', timeout: 15000 });
  await page.waitForTimeout(3000);
  await page.evaluate(() => goTab('totw'));
  await page.waitForTimeout(1500);
  await page.screenshot({ path: 'screenshot_totw.png', fullPage: true });
  await browser.close();
})();
