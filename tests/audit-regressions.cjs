// Regression coverage for the independent visual audit. Synthetic data only.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try {
 const page=await browser.newPage(); const origin=process.env.PORTAL_TEST_URL||'http://127.0.0.1:8769';
 const dir=process.env.PORTAL_ARTIFACT_DIR||'/tmp/portal-browser';fs.mkdirSync(dir,{recursive:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const title='Reader audit '+Date.now();
 const upload=await page.request.post(origin+'/internal/reports',{headers:{'X-Portal-Request':'1','Content-Type':'text/html'},data:`<!doctype html><title>${title}</title><style>body{font:18px system-ui;margin:32px;color:#244d40}section{margin-top:40px;padding:24px;background:#edf3e9;border-radius:12px}</style><h1>${title}</h1><p>This report should fill the available screen.</p><section><h2>Research findings</h2><p>Report content remains readable below the toolbar.</p></section><section><h2>Options and recommendations</h2><p>More useful detail, visible without a tiny embedded window.</p></section>`});
 assert.equal(upload.status(),201);const report=await upload.json();
 for(const [width,height] of [[1440,900],[390,844],[320,568],[844,390]]){
  await page.setViewportSize({width,height});await page.goto(origin+report.reader_url);await page.locator('#report-frame').waitFor();
  const bounds=await page.locator('#report-frame').boundingBox();
  assert(Math.abs(bounds.y+bounds.height-height)<3,JSON.stringify(bounds));assert(bounds.height>height*.65,'reader fills viewport');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal overflow');
  await page.getByRole('link',{name:'Open ↗',exact:true}).waitFor({state:'visible'});
  await page.screenshot({path:`${dir}/fixed-reader-${width}.png`});
 }
 await page.setViewportSize({width:390,height:844});
 const filtered='/?q='+encodeURIComponent(title);
 await page.goto(origin+filtered);await page.locator('.report-card').click();await page.locator('#report-frame').waitFor();
 await page.getByRole('link',{name:'← Library'}).click();await page.locator('.report-card').waitFor();assert.equal(new URL(page.url()).search,new URL(origin+filtered).search);
 await page.getByRole('button',{name:'Filters',exact:true}).click();await page.getByLabel('Collection',{exact:true}).selectOption('General');
 await page.locator('.report-card').waitFor();assert.equal(await page.locator('#filters-toggle').getAttribute('aria-expanded'),'true');
 await page.getByRole('link',{name:'Settings',exact:true}).click();await page.getByRole('heading',{name:'API access',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>document.activeElement.id),'main');
 await page.getByRole('link',{name:'Agent submission example ↓'}).click();
 await page.waitForFunction(()=>document.getElementById('api-guide').getBoundingClientRect().top<innerHeight);
 await page.screenshot({path:dir+'/fixed-settings-guide.png'});
 await page.goto(origin+'/read/not-a-report');await page.getByRole('link',{name:'Back to library',exact:true}).waitFor();
 assert(await page.locator('#header').isVisible());await page.screenshot({path:dir+'/fixed-reader-error.png'});
 await page.goto(origin+'/not-a-page');await page.getByRole('link',{name:'Back to library'}).waitFor();
 await page.goto(origin+'/upload');await page.locator('#drop-zone').waitFor();
 const picker=page.waitForEvent('filechooser');await page.getByText('Drop your report here',{exact:true}).click();await picker;
 await page.getByRole('button',{name:'Import report',exact:true}).click();assert(await page.locator('#upload-status').evaluate(e=>e.classList.contains('error')));
 await page.getByLabel('Report file').setInputFiles({name:'empty.html',mimeType:'text/html',buffer:Buffer.from('')});await page.getByRole('button',{name:'Import report',exact:true}).click();
 await page.getByText('The report HTML is empty. Add content before importing.',{exact:true}).waitFor();await page.screenshot({path:dir+'/fixed-upload-error.png'});
 const shareResponse=await page.request.post(origin+'/internal/shares',{headers:{'X-Portal-Request':'1'},data:{report_id:report.id,password:'example',expires_in_days:1}});assert.equal(shareResponse.status(),201);const share=await shareResponse.json();
 await page.goto(origin+'/shares');const row=page.locator('.shared-row').filter({hasText:title});
 await row.getByRole('button',{name:'Update snapshot / access'}).click();await page.getByLabel('Remove existing password').check();await page.getByLabel('Change expiry').selectOption('never');
 await page.getByRole('button',{name:'Update share',exact:true}).click();await page.getByRole('button',{name:'Done',exact:true}).click();await row.getByText('Anyone with link',{exact:true}).waitFor();assert(await row.getByText('· Expires Never',{exact:true}).isVisible());
 await page.goto(origin+report.reader_url);await page.getByRole('button',{name:'Delete',exact:true}).click();await page.getByRole('button',{name:'Delete report',exact:true}).click();await page.waitForURL(url=>url.pathname==='/');
 assert.equal((await page.request.get(origin+'/internal/reports/'+report.id)).status(),404);assert.equal((await page.request.get(origin+share.url+'files/index.html')).status(),200);
 await page.request.delete(origin+'/internal/shares/'+share.token,{headers:{'X-Portal-Request':'1'}});
 assert.deepEqual(errors,[]);console.log('PASS: reader viewport/screenshots, mobile Open, filters and library context, focus, anchor, recoverable routes, picker, empty upload, live share list, deletion preserves snapshot.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
