// All task traffic is intercepted. Run against a built preview via TEST_BASE_URL.
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true});
  try {
    for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
      const page = await browser.newPage({viewport});
      const errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('http://127.0.0.1:8000/**',r=>r.fulfill({json:[]}));
      let state='paused';
      await page.route('**/task/*/status',r=>r.fulfill({json:{status:state,contract_version:4,phases:[],paper_url:'about:blank'}}));
      const visits=new Map(), sockets=new Map();
      let delayHistory=false, held=null, onHeld=null;
      const row=content=>({id:'shared',msg_type:'agent',agent_type:'PiAgent',content});
      await page.route('**/messages?task_id=*',async route=>{
        const id=new URL(route.request().url()).searchParams.get('task_id');
        visits.set(id,(visits.get(id)||0)+1);
        if(delayHistory){held=route;if(onHeld)onHeld(route);return;}
        await route.fulfill({json:[row(`${id}_${visits.get(id)}`)]});
      });
      await page.routeWebSocket('**/task/*',socket=>{
        sockets.set(socket.url().split('/').pop(),socket);
      });
      const navigate=async id=>{
        const requested=page.waitForRequest(r=>r.url().includes(`/messages?task_id=${id}`));
        await page.evaluate(id=>document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/task/'+id),id);
        const response=await (await requested).response();
        await response.finished();
      };
      const a='aaaaaaaaaaaa',b='bbbbbbbbbbbb';
      await page.goto((process.env.TEST_BASE_URL||'http://127.0.0.1:5187')+'/task/'+a);
      await page.getByText(a+'_1',{exact:true}).filter({visible:true}).waitFor();
      await navigate(b);
      await page.getByText(b+'_1',{exact:true}).filter({visible:true}).waitFor();
      await navigate(a);
      await page.getByText(a+'_2',{exact:true}).filter({visible:true}).waitFor();
      assert.equal(await page.getByText(a+'_1',{exact:true}).count(),0);

      // Reconnect must request history. A live delta arriving during that request wins.
      delayHistory=true;
      const reconnectHistory=new Promise(resolve=>{onHeld=resolve;});
      sockets.get(a).close({code:1012,reason:'test reconnect'});
      held=await reconnectHistory;
      sockets.get(a).send(JSON.stringify(row('LIVE_AFTER_REQUEST')));
      await page.getByText('LIVE_AFTER_REQUEST',{exact:true}).filter({visible:true}).waitFor();
      await held.fulfill({json:[row('OLDER_SNAPSHOT')]});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      assert.equal(await page.getByText('OLDER_SNAPSHOT',{exact:true}).count(),0);

      // A response from a retired connection cannot poison the next visit.
      const nextHistory=new Promise(resolve=>{onHeld=resolve;});
      await page.evaluate(()=>{void document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('task').loadTaskMessages('aaaaaaaaaaaa');});
      const old=await nextHistory;
      delayHistory=false;
      await navigate(b);
      await old.fulfill({json:[row('RETIRED_RESPONSE')]});
      await navigate(a);
      await page.getByText(a+'_'+visits.get(a),{exact:true}).filter({visible:true}).waitFor();
      assert.equal(await page.getByText('RETIRED_RESPONSE',{exact:true}).count(),0);
      await page.getByRole('tab',{name:'论文预览',exact:true}).filter({visible:true}).click();
      await page.getByText('论文草稿，尚未通过最终验收').filter({visible:true}).waitFor();
      state='completed_with_warnings';
      await page.getByText('论文草稿，尚未通过最终验收').filter({visible:true}).waitFor({state:'hidden',timeout:7000});
      state='partial';
      await page.getByText('论文草稿，尚未通过最终验收').filter({visible:true}).waitFor({timeout:7000});
      assert.deepEqual(errors,[]);
      console.log(JSON.stringify({viewport,revisit:'pass',reconnect:'pass',deltaRace:'pass',retiredRequest:'pass',delivery:'pass',errors}));
      await page.screenshot({path:(process.env.TEST_OUTPUT_DIR||'.')+`/reliable-${viewport.width}.png`});
      await page.close();
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
