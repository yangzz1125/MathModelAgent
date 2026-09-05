// Run with PLAYWRIGHT_MODULE pointing to an installed Playwright package.
// Task APIs and WebSockets are mocked; no model jobs or task mutations occur.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const loads = [];
      let statusCalls = 0;
      let serverStatus = 'paused';
      await page.route('**/messages?task_id=*', route => {
        const task = new URL(route.request().url()).searchParams.get('task_id');
        loads.push(task);
        return route.fulfill({ json: [{ id: task, msg_type: 'agent', agent_type: 'PiAgent', content: `MESSAGE_${task}` }] });
      });
      await page.route('**/task/*/status', route => {
        statusCalls++;
        return route.fulfill({ json: { status: serverStatus, model: 'test', thinking: 'high', contract_version: 3, phases: [], paper_url: 'about:blank' } });
      });
      await page.routeWebSocket('**/task/*', () => {});
      await page.goto('http://127.0.0.1:5173/task/aaaaaaaaaaaa');
      await page.getByText('MESSAGE_aaaaaaaaaaaa').filter({ visible: true }).waitFor();
      await page.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/task/bbbbbbbbbbbb'));
      await page.waitForURL('**/task/bbbbbbbbbbbb');
      await page.getByText('MESSAGE_bbbbbbbbbbbb').filter({ visible: true }).waitFor();
      assert.equal(await page.getByText('MESSAGE_aaaaaaaaaaaa').count(), 0);
      assert.deepEqual(loads, ['aaaaaaaaaaaa', 'bbbbbbbbbbbb']);
      const mobile = viewport.width < 768;
      await page.getByRole('tab', { name: mobile ? '论文预览' : '论文预览', exact: true }).filter({ visible: true }).click();
      await page.getByText('论文草稿，尚未通过最终验收').filter({ visible: true }).waitFor();
      const before = statusCalls;
      serverStatus = 'completed';
      await page.getByText('论文草稿，尚未通过最终验收').filter({ visible: true }).waitFor({ state: 'hidden', timeout: 7000 });
      assert.equal(statusCalls - before, 1, 'one page-owned status poll, not per-panel polling');
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ viewport, taskSwitch: 'pass', polling: 'pass', draftState: 'pass', pageErrors: errors }));
      await page.screenshot({ path: `workflow-${viewport.width}.png` });
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
