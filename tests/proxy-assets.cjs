// Real SameSite=Lax authentication proxy + opaque sandbox PNG regression.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
(async()=>{
 const origin=process.env.PORTAL_TEST_URL||'http://127.0.0.1:8769';
 const proxy=spawn('python3',['-u','-c',`
import http.client,sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
upstream=urlsplit(sys.argv[1])
class Proxy(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_HEAD(self):self.route()
 def do_GET(self):self.route()
 def do_POST(self):self.route()
 def do_PATCH(self):self.route()
 def do_DELETE(self):self.route()
 def route(self):
  path=urlsplit(self.path).path
  if path=='/login':
   data=b'<!doctype html><title>Sign in</title><h1>Test login page</h1>'
   self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(data)));self.end_headers()
   if self.command!='HEAD':self.wfile.write(data)
   return
  cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
  if not path.startswith(('/api/','/share/')) and ('portal_test_login' not in cookie or cookie['portal_test_login'].value!='valid'):
   self.send_response(302);self.send_header('Location','/login');self.send_header('Content-Type','text/html');self.send_header('Content-Length','0');self.end_headers();return
  headers={k:v for k,v in self.headers.items() if k.lower() not in {'host','connection','x-portal-public'}}
  headers['Host']=upstream.netloc;headers['X-Portal-Public']='1' if path.startswith('/share/') else '0'
  body=self.rfile.read(int(self.headers.get('Content-Length','0')))
  connection=http.client.HTTPConnection(upstream.hostname,upstream.port,timeout=15)
  connection.request(self.command,self.path,body=body,headers=headers);r=connection.getresponse();data=r.read()
  self.send_response(r.status)
  for k,v in r.getheaders():
   if k.lower() not in {'server','date','connection','transfer-encoding','content-length'}:self.send_header(k,v)
  self.send_header('Content-Length',r.getheader('Content-Length',str(len(data))));self.end_headers()
  if self.command!='HEAD':self.wfile.write(data)
  connection.close()
server=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
print(server.server_port,flush=True);server.serve_forever()
`,origin],{stdio:['ignore','pipe','inherit']});
 const proxyPort=await new Promise((resolve,reject)=>{proxy.stdout.once('data',d=>resolve(Number(d.toString().trim())));proxy.once('error',reject);});
 const base='http://127.0.0.1:'+proxyPort;
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 let report,share,key;
 const context=await browser.newContext();const page=await context.newPage();
 try{
  key=(await (await context.request.get(origin+'/internal/settings')).json()).api_key;
  const zip=execFileSync('python3',['-c',`
import io,zipfile,sys,struct,zlib
b=io.BytesIO()
def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
png=b'\\x89PNG\\r\\n\\x1a\\n'+chunk(b'IHDR',struct.pack('!2I5B',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\\x00\\x20\\x80\\x40'))+chunk(b'IEND',b'')
with zipfile.ZipFile(b,'w') as z:
 z.writestr('index.html','''<!doctype html><title>Proxy asset validation</title><link rel="stylesheet" href="assets/style.css"><h1>Packaged PNG</h1><img src="assets/pixel.png" alt="Packaged PNG"><p id="boundary"></p><script src="assets/report.js"></script>''')
 z.writestr('assets/pixel.png',png)
 z.writestr('assets/style.css','body{color:rgb(32,128,64);font:18px system-ui}')
 z.writestr('assets/report.js',"fetch('/internal/settings').then(r=>r.text()).then(()=>document.getElementById('boundary').textContent='UNSAFE').catch(()=>document.getElementById('boundary').textContent='Isolated')")
sys.stdout.buffer.write(b.getvalue())`]);
  const created=await context.request.post(origin+'/api/reports',{headers:{'X-Api-Key':key,'Content-Type':'application/zip'},data:zip});assert.equal(created.status(),201);report=await created.json();
  const anonymous=await browser.newContext();
  assert.equal((await anonymous.request.get(base+report.url,{maxRedirects:0})).status(),302,'private document still requires login');
  assert.equal((await anonymous.request.get(base+'/internal/settings',{maxRedirects:0})).status(),302,'settings remain private');
  assert.equal((await anonymous.request.get(base+'/api/reports')).status(),401,'API remains keyed');
  await context.addCookies([{name:'portal_test_login',value:'valid',url:base,httpOnly:true,sameSite:'Lax'}]);
  const imageResponses=[];page.on('response',r=>{if(r.url().includes('/assets/pixel.png'))imageResponses.push(r);});
  const documentResponse=page.waitForResponse(r=>r.url().includes('/share/_reader/')&&r.url().endsWith('/files/index.html'));
  await page.goto(base+report.reader_url);
  const frame=page.frameLocator('#report-frame');await frame.getByText('Isolated',{exact:true}).waitFor();
  const image=frame.getByRole('img',{name:'Packaged PNG'});
  await image.waitFor();assert(await image.evaluate(async i=>{await i.decode();return i.naturalWidth===1;}),'private PNG renders');
  assert.equal(await frame.locator('body').evaluate(e=>getComputedStyle(e).color),'rgb(32, 128, 64)','relative stylesheet renders');
  const doc=await documentResponse;assert(doc.headers()['content-security-policy'].includes('sandbox'));
  assert(!doc.headers()['content-security-policy'].includes('allow-same-origin'));
  assert.equal(imageResponses.length,1);assert.equal(imageResponses[0].status(),200);assert.equal(imageResponses[0].headers()['content-type'],'image/png');
  const imageHeaders=await imageResponses[0].request().allHeaders();assert(!imageHeaders.cookie,'sandboxed PNG request has no login cookie');
  assert(imageResponses[0].url().includes('/share/_reader/'),'relative asset uses scoped view');
  assert(await image.evaluate(()=>{try{parent.document.body;return false;}catch(e){return true;}}),'parent DOM stays isolated');
  assert(await image.evaluate(()=>{try{localStorage.getItem('test');return false;}catch(e){return true;}}),'storage stays isolated');
  const oldFrame=await page.locator('#report-frame').getAttribute('src');await page.reload();await page.frameLocator('#report-frame').getByText('Isolated',{exact:true}).waitFor();
  assert.equal(await page.locator('#report-frame').getAttribute('src'),oldFrame,'stable private URL on refresh');
  const plain=await context.request.post(origin+'/api/shares',{headers:{'X-Api-Key':key},data:{report_id:report.id}});assert.equal(plain.status(),201);share=await plain.json();
  const shared=await anonymous.newPage();await shared.goto(base+share.url);assert(await shared.getByRole('img',{name:'Packaged PNG'}).evaluate(async i=>{await i.decode();return i.naturalWidth===1;}),'anonymous shared PNG renders');
  const changed=await context.request.patch(origin+'/api/shares/'+share.token,{headers:{'X-Api-Key':key},data:{password:'synthetic-proxy-password'}});assert.equal(changed.status(),200);
  assert.equal((await anonymous.request.get(base+share.url+'files/assets/pixel.png')).status(),403,'protected snapshot PNG denied before unlock');
  await shared.goto(base+share.url);await shared.getByLabel('Password',{exact:true}).fill('synthetic-proxy-password');await shared.getByRole('button',{name:'Open report',exact:true}).click();
  assert(await shared.getByRole('img',{name:'Packaged PNG'}).evaluate(async i=>{await i.decode();return i.naturalWidth===1;}),'password snapshot PNG renders');
  assert(shared.url().includes('/session/'),'unlocked assets use scoped password session');
  console.log('PASS: real Lax-cookie proxy, PNG/CSS/JS private assets without cookies or ORB, sandbox DOM/storage/API isolation, fresh reopening, anonymous and password snapshots.');
 }finally{
  if(share)await context.request.delete(origin+'/api/shares/'+share.token,{headers:{'X-Api-Key':key}});
  if(report)await context.request.delete(origin+'/internal/reports/'+report.id,{headers:{'X-Portal-Request':'1'}});
  await browser.close();proxy.kill();
 }
})().catch(e=>{console.error(e);process.exit(1);});
