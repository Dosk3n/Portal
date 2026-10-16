// Real browser coverage for explicit revisions, restore and bounded history.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try {
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 const origin=process.env.PORTAL_TEST_URL||'http://127.0.0.1:8769';
 const dir=process.env.PORTAL_ARTIFACT_DIR||'/tmp/portal-browser';fs.mkdirSync(dir,{recursive:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const unique='Conversation '+Date.now();
 const html=word=>`<!doctype html><title>${unique}</title><h1>${word}</h1><p>Discussion notes for ${word}.</p>`;
 const create=await page.request.post(origin+'/internal/reports',{headers:{'X-Portal-Request':'1','Content-Type':'text/html'},data:html('First')});
 assert.equal(create.status(),201);const report=await create.json();
 const shares=await page.request.post(origin+'/internal/shares',{headers:{'X-Portal-Request':'1'},data:{report_id:report.id,expires_in_days:7}});
 assert.equal(shares.status(),201);const share=await shares.json();
 await page.goto(origin+report.reader_url);
 async function revise(word,zip=false){
  await page.getByRole('button',{name:'Update',exact:true}).click();
  await page.getByRole('button',{name:'Upload revision',exact:true}).click();
  let buffer=Buffer.from(html(word));
  if(zip)buffer=execFileSync('python3',['-c',`import io,sys,zipfile
b=io.BytesIO()
with zipfile.ZipFile(b,'w') as z:
 z.writestr('index.html',${JSON.stringify(html(word)+'<img src="asset.svg" alt="Revision asset">')})
 z.writestr('asset.svg','<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#244d40"/></svg>')
sys.stdout.buffer.write(b.getvalue())`]);
  await page.getByLabel('Revised report file').setInputFiles({name:zip?'revised.zip':'revised.html',mimeType:zip?'application/zip':'text/html',buffer});
  await page.getByRole('button',{name:'Save revision',exact:true}).click();
  await page.frameLocator('#report-frame').getByRole('heading',{name:word,exact:true}).waitFor();
  assert.equal(new URL(page.url()).pathname,report.reader_url);
  if(zip)assert(await page.frameLocator('#report-frame').getByRole('img',{name:'Revision asset'}).evaluate(img=>img.complete&&img.naturalWidth>0));
 }
 await revise('Second',true);await revise('Third');
 const snap=await page.request.get(origin+share.url+'files/index.html');assert((await snap.text()).includes('First'));
 await page.getByRole('button',{name:'Update',exact:true}).click();await page.getByRole('button',{name:'Version history',exact:true}).click();
 await page.getByRole('link',{name:'View version 1 ↗',exact:true}).waitFor();
 await page.screenshot({path:dir+'/revision-history-desktop.png'});
 const versionOne=await page.getByRole('link',{name:'View version 1 ↗',exact:true}).getAttribute('href');
 await page.getByRole('button',{name:'Restore version 1',exact:true}).click();
 await page.getByRole('button',{name:'Restore version',exact:true}).click();
 await page.frameLocator('#report-frame').getByRole('heading',{name:'First',exact:true}).waitFor();
 assert.equal((await page.request.get(origin+versionOne)).status(),404);
 await page.getByRole('button',{name:'Update',exact:true}).click();await page.getByRole('button',{name:'Edit details',exact:true}).click();
 await page.getByLabel('Title',{exact:true}).fill(unique+' edited');await page.getByLabel('Collection',{exact:true}).fill('Conversations');
 await page.getByRole('button',{name:'Save details',exact:true}).click();
 await page.locator('.reader-info h1').getByText(unique+' edited',{exact:true}).waitFor();
 await revise('Fourth',true);
 for(const [width,height] of [[1440,900],[390,844],[320,568],[844,390]]){
  await page.setViewportSize({width,height});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'reader no horizontal overflow');
  const box=await page.locator('#report-frame').boundingBox();assert(Math.abs(box.y+box.height-height)<3);assert(box.height>height*.65);
  if(width===390)await page.screenshot({path:dir+'/revision-reader-mobile.png'});
 }
 await page.setViewportSize({width:1440,height:900});await page.goto(origin+'/shares');
 await page.locator('.shared-row').filter({hasText:unique}).getByRole('button',{name:'Update snapshot / access'}).click();
 await page.getByLabel('Update snapshot to the latest version of this report').check();
 await page.getByRole('button',{name:'Update share',exact:true}).click();await page.getByLabel('Created share URL').waitFor();
 assert.equal(await page.getByLabel('Created share URL').inputValue(),origin+share.url);
 assert((await (await page.request.get(origin+share.url+'files/index.html')).text()).includes('Fourth'));
 await page.goto(origin+'/settings');
 await page.getByLabel('Versions to keep',{exact:true}).fill('1');await page.getByRole('button',{name:'Save version retention',exact:true}).click();
 await page.getByRole('heading',{name:'Remove older versions?',exact:true}).waitFor();
 await page.getByRole('button',{name:'Cancel',exact:true}).click();
 assert.equal((await (await page.request.get(origin+'/internal/reports/'+report.id+'/versions')).json()).versions.length,3);
 await page.getByRole('button',{name:'Save version retention',exact:true}).click();
 const pruneResponse=page.waitForResponse(r=>r.url()===origin+'/internal/settings'&&r.request().method()==='PATCH');
 await page.getByRole('button',{name:'Save and remove older versions',exact:true}).click();assert.equal((await pruneResponse).status(),200);
 await page.getByLabel('Versions to keep',{exact:true}).waitFor();assert.equal(await page.getByLabel('Versions to keep',{exact:true}).inputValue(),'1');
 assert.equal((await (await page.request.get(origin+'/internal/reports/'+report.id+'/versions')).json()).versions.length,1);
 assert((await (await page.request.get(origin+share.url+'files/index.html')).text()).includes('Fourth'));
 await page.getByLabel('Versions to keep',{exact:true}).fill('3');
 const saveResponse=page.waitForResponse(r=>r.url()===origin+'/internal/settings'&&r.request().method()==='PATCH');
 await page.getByRole('button',{name:'Save version retention',exact:true}).click();assert.equal((await saveResponse).status(),200);
 await page.getByText('Version retention saved.',{exact:true}).waitFor();
 await page.request.delete(origin+'/internal/reports/'+report.id,{headers:{'X-Portal-Request':'1'}});
 await page.request.delete(origin+'/internal/shares/'+share.token,{headers:{'X-Portal-Request':'1'}});
 assert.deepEqual(errors,[]);
 console.log('PASS: revision uploads/assets, stable report link, history, restore, metadata edits, independent share refresh, retention confirmation/cancel/pruning, mobile reader.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
