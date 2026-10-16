// Run using playwright-core and a local Chromium executable; see docs/validation.md.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs');

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
  const context=await browser.newContext();
  const page=await context.newPage();
  const origin=process.env.PORTAL_TEST_URL||'http://127.0.0.1:8769';
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const testTitle='Research browser fixture '+Date.now();
  const zip=execFileSync('python3',['-c',`import io,zipfile,sys
b=io.BytesIO()
with zipfile.ZipFile(b,'w') as z:
 z.writestr('index.html','''<!doctype html><title>${testTitle}</title><meta name="description" content="A synthetic comparison report for browser validation."><style>body{font:18px system-ui;margin:50px;color:#244d40}img{width:100px}</style><h1>${testTitle}</h1><p>Research, sources and a useful comparison.</p><img src="assets/image.svg" alt="Packaged illustration"><button id="check">Check interactivity</button><p id="result"></p><p id="boundary"></p><script>document.getElementById('check').onclick=()=>document.getElementById('result').textContent='The report script works.';fetch('/internal/settings').then(r=>r.text()).then(()=>document.getElementById('boundary').textContent='UNSAFE').catch(()=>document.getElementById('boundary').textContent='Isolated');</script>''')
 z.writestr('assets/image.svg','<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#244d40"/></svg>')
sys.stdout.buffer.write(b.getvalue())`]);
  await page.goto(origin+'/upload');
  await page.getByLabel('Report file').setInputFiles({name:'report.zip',mimeType:'application/zip',buffer:zip});
  await page.getByLabel('Collection',{exact:true}).fill('Research');
  await page.getByLabel('Tags',{exact:true}).fill('browser,comparison');
  await page.getByRole('button',{name:'Import report',exact:true}).click();
  await page.waitForURL('**/read/**');
  const reader=page.url();
  const frame=page.frameLocator('#report-frame');
  await frame.getByRole('button',{name:'Check interactivity'}).click();
  await frame.getByText('The report script works.',{exact:true}).waitFor();
  await frame.getByText('Isolated',{exact:true}).waitFor();
  assert(await frame.getByRole('img').evaluate(img=>img.complete&&img.naturalWidth>0),'private packaged image');
  await page.getByRole('button',{name:'Share externally'}).click();
  await page.getByLabel('Password (optional)',{exact:true}).fill('browser-password');
  await page.getByRole('button',{name:'Create snapshot link'}).click();
  const share=await page.getByLabel('Created share URL').inputValue();
  const shareToken=new URL(share).pathname.split('/')[2];
  const anonymous=await browser.newContext();const publicPage=await anonymous.newPage();
  await publicPage.goto(share);
  await publicPage.getByLabel('Password',{exact:true}).fill('browser-password');
  await publicPage.getByRole('button',{name:'Open report',exact:true}).click();
  await publicPage.getByRole('heading',{name:testTitle,exact:true}).waitFor();
  await publicPage.getByRole('button',{name:'Check interactivity'}).click();
  await publicPage.getByText('The report script works.',{exact:true}).waitFor();
  assert(await publicPage.getByRole('img').evaluate(img=>img.complete&&img.naturalWidth>0),'password-protected image');
  await page.goto(origin+'/settings');
  await page.getByRole('heading',{name:'API access',exact:true}).waitFor();
  const key=await page.getByLabel('API key',{exact:true}).inputValue();
  const unauth=await context.request.get(origin+'/api/reports');assert.equal(unauth.status(),401);
  const replacementTitle='Updated comparison '+Date.now();
  const uploaded=await context.request.post(origin+'/api/reports',{headers:{'X-Api-Key':key,'Content-Type':'text/html','X-Report-Topic':'Research'},data:`<title>${replacementTitle}</title><h1>${replacementTitle}</h1><p>The newly selected report.</p>`});
  assert.equal(uploaded.status(),201);const replacement=await uploaded.json();
  await page.getByRole('link',{name:'Shares',exact:true}).click();
  const row=page.locator('.shared-row').filter({hasText:testTitle});
  await row.getByRole('button',{name:'Update snapshot / access'}).click();
  await page.getByLabel('Find a replacement report (optional)').fill(replacementTitle);
  await page.locator('[data-choose-report="'+replacement.id+'"]').click();
  await page.getByRole('button',{name:'Update share',exact:true}).click();
  assert.equal(await page.getByLabel('Created share URL').inputValue(),share);
  await publicPage.goto(share);await publicPage.getByRole('heading',{name:replacementTitle,exact:true}).waitFor();
  await page.goto(origin+'/settings');
  await page.getByRole('button',{name:'Regenerate',exact:true}).click();
  await page.getByRole('button',{name:'Regenerate key',exact:true}).click();
  await page.getByText('API key regenerated.',{exact:true}).waitFor();
  await page.getByRole('heading',{name:'API access',exact:true}).waitFor();
  await page.waitForFunction(previous=>document.getElementById('api-key')?.value!==previous && Boolean(document.getElementById('api-key')),key);
  assert.equal((await context.request.get(origin+'/api/reports',{headers:{'X-Api-Key':key}})).status(),401);
  const existing=page.locator('.watch-row').filter({hasText:'/storage/inbox'});
  if(await existing.count()){
    await existing.getByRole('button',{name:'Remove location',exact:true}).click();
    await existing.waitFor({state:'detached'});
  }
  await page.getByLabel('Add a folder inside the container').fill('/storage/inbox');
  await page.getByRole('button',{name:'Add location',exact:true}).click();
  await page.locator('.watch-path').filter({hasText:'/storage/inbox'}).waitFor();
  await page.getByLabel('Enable folder watching',{exact:true}).check();
  await page.getByText('Folder watching enabled.',{exact:true}).waitFor();
  await page.getByLabel('Enable folder watching',{exact:true}).uncheck();
  await page.getByText('Folder watching disabled.',{exact:true}).waitFor();
  await page.goto(origin+'/shares');
  await page.locator('[data-revoke="'+shareToken+'"]').click();
  await page.getByRole('button',{name:'Revoke link',exact:true}).click();
  await page.getByText('Share revoked.',{exact:true}).waitFor();
  assert.equal((await anonymous.request.get(share)).status(),404);
  await page.goto(origin+'/?q='+encodeURIComponent(testTitle));
  await page.getByRole('heading',{name:testTitle,exact:true}).waitFor();
  assert.equal(await page.locator('.report-card').count(),1);
  await page.goto(origin+'/');await page.locator('.report-card').first().waitFor();
  await page.setViewportSize({width:1440,height:1050});
  const artifactDir=process.env.PORTAL_ARTIFACT_DIR||'/tmp/portal-browser';fs.mkdirSync(artifactDir,{recursive:true});
  await page.screenshot({path:artifactDir+'/portal-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:artifactDir+'/portal-mobile.png',fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile horizontal overflow');
  const theme=page.getByRole('switch',{name:'Dark mode',exact:true});
  await theme.click();assert.equal(await theme.getAttribute('aria-checked'),'true');
  await page.reload();await page.locator('.report-card').first().waitFor();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'dark','theme survives reload');
  await theme.focus();await page.keyboard.press('Space');
  assert.equal(await theme.getAttribute('aria-checked'),'false','keyboard switches back to light');
  await page.getByRole('button',{name:'Filters',exact:true}).click();
  await page.getByLabel('Collection',{exact:true}).selectOption('Research');
  await page.locator('.report-card').first().waitFor();
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS: manual ZIP upload, private scripts/assets/isolation, password share assets, keyed API, replacement URL/access preservation, key rotation, watcher settings, revocation, search and mobile layout.');
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
