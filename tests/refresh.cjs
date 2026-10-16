// Exercise the real poll callback without waiting 30 seconds between checks.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
  try {
    const context=await browser.newContext({viewport:{width:390,height:600}});
    const page=await context.newPage();
    const origin=process.env.PORTAL_TEST_URL||'http://127.0.0.1:8769';
    const title='Quiet refresh '+Date.now();
    await page.addInitScript(()=>{
      const original=window.setInterval;
      window.setInterval=(callback,delay,...args)=>{
        if(delay===30000)window.__libraryPoll=()=>callback(...args);
        return original(callback,delay,...args);
      };
    });
    const poll=()=>page.evaluate(async()=>{await window.__libraryPoll();await Promise.resolve();});
    const upload=async suffix=>{
      const response=await context.request.post(origin+'/internal/reports',{
        headers:{'X-Portal-Request':'1','Content-Type':'text/html','X-Report-Topic':'Refresh checks'},
        data:`<title>${title} ${suffix}</title><p>Synthetic report for background refresh validation.</p>`
      });
      assert.equal(response.status(),201);return response.json();
    };
    await page.goto(origin+'/?q='+encodeURIComponent(title));
    await page.getByRole('heading',{name:'Nothing matches just yet.'}).waitFor();
    await page.evaluate(()=>{
      window.__search=document.getElementById('search');
      window.__mainReplacements=0;
      new MutationObserver(records=>{
        for(const record of records)if(record.target===document.getElementById('main') && record.removedNodes.length)window.__mainReplacements++;
      }).observe(document.getElementById('main'),{childList:true});
    });
    await poll();await poll();
    assert(await page.evaluate(()=>document.getElementById('search')===window.__search && window.__mainReplacements===0),'unchanged polls must preserve the visible page');
    const first=await upload('first');await poll();
    await page.locator(`[data-report-id="${first.id}"]`).waitFor();
    await page.evaluate(()=>{window.__firstCard=document.querySelector('.report-card');});
    await poll();
    assert(await page.evaluate(()=>document.querySelector('.report-card')===window.__firstCard),'unchanged cards retain their DOM nodes');

    // An in-flight refresh must not overwrite text the user started typing.
    await page.route('**/internal/facets',async route=>{
      await page.getByLabel('Search titles, contents and tags').fill('unfinished search');
      await route.continue();
    });
    const second=await upload('second');await poll();
    assert.equal(await page.locator('.report-card').count(),1);
    assert.equal(await page.getByLabel('Search titles, contents and tags').inputValue(),'unfinished search');
    await page.unroute('**/internal/facets');
    await page.evaluate(()=>document.activeElement.blur());await poll();
    await page.locator(`[data-report-id="${second.id}"]`).waitFor();
    assert.equal(await page.getByLabel('Search titles, contents and tags').inputValue(),'unfinished search');

    // Preserve the mobile filter panel and the report being read while new cards arrive.
    await page.getByRole('button',{name:'Filters',exact:true}).click();
    await page.evaluate(()=>document.activeElement.blur());
    await page.evaluate(()=>window.scrollTo(0,document.querySelector('.report-card').getBoundingClientRect().top+window.scrollY-100));
    const anchor=await page.evaluate(()=>{
      const card=[...document.querySelectorAll('.report-card')].find(node=>{const rect=node.getBoundingClientRect();return rect.bottom>0 && rect.top<innerHeight;});
      return {id:card.dataset.reportId,top:card.getBoundingClientRect().top};
    });
    await upload('third');await poll();
    assert.equal(await page.locator('.report-card').count(),3);
    assert.equal(await page.locator('#filters-toggle').getAttribute('aria-expanded'),'true');
    const top=await page.locator(`[data-report-id="${anchor.id}"]`).evaluate(node=>node.getBoundingClientRect().top);
    assert(Math.abs(top-anchor.top)<2,'poll preserves the visible report position');
    assert(await page.evaluate(()=>document.getElementById('search')===window.__search && window.__mainReplacements===0),'updates never rebuild the library shell');

    await page.route('**/internal/reports?*',route=>route.abort());
    await poll();
    assert.equal(await page.locator('.report-card').count(),3,'a network failure keeps the last successful results');
    await page.unroute('**/internal/reports?*');
    await page.route('**/internal/facets',async route=>{
      await page.getByRole('link',{name:'Settings',exact:true}).click();
      await route.continue();
    });
    await poll();
    await page.getByRole('heading',{name:'API access',exact:true}).waitFor();
    assert.equal(await page.locator('.reports-grid').count(),0,'stale polls cannot replace another page');
    console.log('PASS: no flicker on unchanged polls; imports update in place; typing, filters and scroll survive; failures and navigation retain the correct page.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
