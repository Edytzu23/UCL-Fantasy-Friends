const { chromium } = require('./node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.goto('http://localhost:8000/');
  await page.waitForLoadState('networkidle');
  await page.click('text=TOTW');
  await page.waitForTimeout(4000);
  const info = await page.$$eval('.totw-half', els => els.map((e,i) => ({
    idx: i, w: e.offsetWidth, top: e.offsetTop
  })));
  console.log('terrains:', JSON.stringify(info));
  await page.screenshot({ path: 'f:/Claude Folder/FF Dashboard/totw_check.png', fullPage: true });
  await browser.close();
})();
