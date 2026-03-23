const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto('http://localhost:3000/main-mockup.html');
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: 'mockup_v1.png' });
  await browser.close();
  console.log('Screenshot saved!');
})().catch(e => { console.error(e); process.exit(1); });
