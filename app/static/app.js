(() => {
  'use strict';
  const main = document.getElementById('main');
  const dialog = document.getElementById('dialog');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString([], {dateStyle:'medium', timeStyle:'short'}) : 'Never';
  let routeController;
  let settings;
  let selectedFile;
  let currentReport;
  let generation = 0;
  let searchTimer;
  let shareSelection;
  let toastTimer;
  let libraryState;
  let libraryRefreshInFlight=false;
  let libraryUrl='/';
  let filtersOpen=false;
  let renderedUrl='';
  const params = () => new URLSearchParams(location.search);

  async function request(path, {method='GET', body, signal}={}) {
    const headers = {Accept:'application/json'};
    if (method !== 'GET') headers['X-Portal-Request']='1';
    if (body && !(body instanceof FormData)) { headers['Content-Type']='application/json'; body=JSON.stringify(body); }
    const response = await fetch('/internal'+path, {method, headers, body, signal});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
    return result;
  }

  function toast(message) {
    const element=document.getElementById('toast'); element.textContent=message; element.hidden=false;
    clearTimeout(toastTimer);toastTimer=setTimeout(() => {element.hidden=true;},4000);
  }
  function heading(title,description,action='') { return `<div class="section-heading"><div><p class="eyebrow">Your personal report library</p><h1>${title}</h1><p>${description}</p></div>${action}</div>`; }
  function errorMessage(error) { return `<p class="error" role="alert">${esc(error.message)}</p>`; }
  function navigate(url,replace=false) {
    if(location.pathname==='/' || location.pathname==='/index.html')libraryUrl=location.pathname+location.search;
    history[replace?'replaceState':'pushState']({},'',url); render(true);
  }
  function closeDialog() {dialog.close();dialog.replaceChildren();}
  function openDialog(content) {dialog.innerHTML=content;dialog.showModal();}
  const dialogTop = title => `<div class="dialog-top"><h2 id="dialog-title">${title}</h2><button class="dialog-close" data-close aria-label="Close">×</button></div>`;
  function filters(patch) {
    const p=params();p.delete('page');
    for(const [key,value] of Object.entries(patch)) {if(value)p.set(key,value);else p.delete(key);}
    navigate('/'+(p.size?'?'+p:''));
  }

  async function render(focus=false) {
    renderedUrl=location.pathname+location.search;
    routeController?.abort();routeController=new AbortController();
    libraryState=null;
    const id=++generation;
    closeDialog();currentReport=null;
    document.body.classList.toggle('reader-mode',location.pathname.startsWith('/read/'));
    document.getElementById('header').hidden=location.pathname.startsWith('/read/');
    main.className=location.pathname.startsWith('/read/')?'reader-shell':'page-shell';
    for(const link of document.querySelectorAll('.top-nav a')) {if(link.pathname===location.pathname)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
    main.innerHTML='<div class="state-panel"><h3>Opening your library…</h3></div>';
    try {
      if(location.pathname==='/settings')await settingsPage(id);
      else if(location.pathname==='/upload')uploadPage();
      else if(location.pathname==='/shares')await sharesPage(id);
      else if(location.pathname.startsWith('/read/'))await readerPage(id);
      else await libraryPage(id);
      if(id===generation){
        if(focus===true)main.focus({preventScroll:true});
        if(location.hash)document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
      }
    } catch(error) {if(error.name!=='AbortError' && id===generation){main.innerHTML=`<div class="state-panel"><h3>Couldn’t open this page.</h3>${errorMessage(error)}<div class="actions"><button class="primary-button" data-retry>Try again</button><a class="secondary-button" href="${esc(libraryUrl)}" data-nav>Back to library</a>${currentReport?'<button class="text-button danger" data-delete-report>Delete</button>':''}</div></div>`;document.body.classList.remove('reader-mode');document.getElementById('header').hidden=false;main.className='page-shell';}}
  }

  async function libraryPage(id) {
    const p=params();
    const [data,facets]=await Promise.all([request('/reports?'+p,{signal:routeController.signal}),request('/facets',{signal:routeController.signal})]);
    if(id!==generation)return;
    const options=(items,value,label)=>`<option value="">${label}</option>`+items.map(item=>`<option value="${esc(item.name)}"${value===item.name?' selected':''}>${esc(item.name)} (${item.count})</option>`).join('');
    document.title='Report library · Portal';
    main.innerHTML=heading('All your reports. One place.','Research, discoveries, and decisions. Saved for later.','<a class="primary-button" href="/upload" data-nav>+ Add report</a>')+
      `<div class="search-strip"><form class="search-box" id="search-form" role="search"><label class="sr-only" for="search">Search titles, contents and tags</label><span aria-hidden="true">⌕</span><input type="search" id="search" placeholder="Search titles, contents and tags…" value="${esc(p.get('q')||'')}" autocomplete="off"><button class="text-button" type="submit">Search</button></form><button class="filter-toggle" id="filters-toggle" aria-expanded="${filtersOpen}" aria-controls="filter-panel">Filters</button></div>
      <div class="library-layout"><aside class="filter-panel${filtersOpen?' is-open':''}" id="filter-panel"><div class="filter-heading"><h2>Refine your library</h2><button class="text-button" data-reset>Reset</button></div><div class="filter-fields">
      <div class="field"><label for="topic">Collection</label><select id="topic" data-filter="topic">${options(facets.topics,p.get('topic'),'All collections')}</select></div>
      <div class="field"><label for="source">Source</label><select id="source" data-filter="source">${options(facets.sources,p.get('source'),'All sources')}</select></div>
      <div class="field"><label for="tag">Tag</label><select id="tag" data-filter="tag">${options(facets.tags,p.get('tag'),'All tags')}</select></div>
      <div class="field"><label for="from">From date</label><input type="date" id="from" data-filter="from" value="${esc(p.get('from')||'')}"></div><div class="field"><label for="to">To date</label><input type="date" id="to" data-filter="to" value="${esc(p.get('to')||'')}"></div></div><p class="filter-note" id="library-count">${facets.total} reports in your library.<br>New submissions appear automatically.</p></aside>
      <section class="report-workspace" aria-label="Reports"><div class="results-toolbar"><div><h2>${p.size?'Matching reports':'Your reports'}</h2><p class="results-summary" aria-live="polite">${data.total} ${data.total===1?'report':'reports'}</p></div><div class="sort-field"><label for="sort">Sort by</label><select id="sort" data-filter="sort">${[['newest','Newest first'],['oldest','Oldest first'],['title','Title A–Z']].map(([v,t])=>`<option value="${v}"${(p.get('sort')||'newest')===v?' selected':''}>${t}</option>`).join('')}</select></div></div>
      <div class="reports-grid">${data.reports.length?data.reports.map(card).join(''):emptyReports(p)}</div>
      <div id="library-pagination">${pagination(data)}</div></section></div><footer class="page-footer"><span>Made to keep the useful things close.</span><span>Private until you choose to share.</span></footer>`;
    libraryState={key:location.pathname+location.search,data,facets};
  }

  function emptyReports(p) {return `<div class="state-panel"><h3>${p.size?'Nothing matches just yet.':'A home for your next report.'}</h3><p>${p.size?'Try a different search or clear your filters.':'Upload a report or ask an agent to send one to Portal.'}</p><a class="primary-button" href="/upload" data-nav>Add a report</a></div>`;}
  function pagination(data) {return data.pages>1?`<nav class="pagination" aria-label="Pages"><span class="pagination-detail">Page ${data.page} of ${data.pages}</span><div class="pagination-links"><button class="page-link" data-page="${data.page-1}" ${data.page===1?'disabled':''}>← Previous</button><button class="page-link" data-page="${data.page+1}" ${data.page===data.pages?'disabled':''}>Next →</button></div></nav>`:'';}
  function card(r) {return `<a class="report-card" data-report-id="${esc(r.id)}" data-nav href="${esc(r.reader_url)}"><div class="card-meta"><span class="source-indicator"></span><span class="card-source">${esc(r.source)}</span><span>·</span><time>${esc(r.date)}</time></div><h3>${esc(r.title)}</h3><p class="card-excerpt">${esc(r.excerpt||'Open this report to take a look inside.')}</p>${r.tags.length?`<div class="tag-list">${r.tags.slice(0,3).map(t=>`<span class="tag-pill">${esc(t)}</span>`).join('')}</div>`:''}<div class="card-footer"><span class="topic-tag">${esc(r.topic)}</span><span class="card-arrow">↗</span></div></a>`;}

  function canRefreshLibrary() {
    const focused=document.activeElement;
    return location.pathname==='/' && libraryState?.key===location.pathname+location.search && !document.hidden && !dialog.open &&
      !(main.contains(focused) && focused.matches('input,select,textarea,button,a'));
  }

  async function refreshLibrary() {
    if(libraryRefreshInFlight || !canRefreshLibrary())return;
    libraryRefreshInFlight=true;
    const id=generation,key=location.pathname+location.search,signal=routeController.signal;
    try {
      const [data,facets]=await Promise.all([request('/reports?'+params(),{signal}),request('/facets',{signal})]);
      if(signal.aborted || id!==generation || key!==location.pathname+location.search || !canRefreshLibrary())return;
      const previous=libraryState;
      const scrollY=window.scrollY;
      const grid=main.querySelector('.reports-grid');
      const anchor=scrollY>0?[...grid.querySelectorAll('.report-card')].find(node=>{const rect=node.getBoundingClientRect();return rect.bottom>0 && rect.top<innerHeight;}):null;
      const anchorTop=anchor?.getBoundingClientRect().top;
      if(JSON.stringify(data)!==JSON.stringify(previous.data)) {
        if(!data.reports.length)grid.innerHTML=emptyReports(params());
        else {
          grid.querySelector('.state-panel')?.remove();
          const nodes=new Map([...grid.querySelectorAll('.report-card')].map(node=>[node.dataset.reportId,node]));
          const oldReports=new Map(previous.data.reports.map(report=>[report.id,JSON.stringify(report)]));
          let cursor=grid.firstChild;
          for(const report of data.reports) {
            let node=nodes.get(report.id);
            if(!node || oldReports.get(report.id)!==JSON.stringify(report)) {
              const template=document.createElement('template');template.innerHTML=card(report);
              const replacement=template.content.firstElementChild;
              if(node){if(cursor===node)cursor=replacement;node.replaceWith(replacement);}
              node=replacement;
            }
            if(node!==cursor)grid.insertBefore(node,cursor);
            cursor=node.nextSibling;
            nodes.delete(report.id);
          }
          for(const node of nodes.values())node.remove();
        }
        if(data.total!==previous.data.total)main.querySelector('.results-summary').textContent=`${data.total} ${data.total===1?'report':'reports'}`;
        if(data.page!==previous.data.page || data.pages!==previous.data.pages)document.getElementById('library-pagination').innerHTML=pagination(data);
      }
      for(const [field,list,label] of [['topic','topics','All collections'],['source','sources','All sources'],['tag','tags','All tags']]) {
        if(JSON.stringify(facets[list])!==JSON.stringify(previous.facets[list])) {
          const select=document.getElementById(field),value=select.value;
          select.replaceChildren(new Option(label,''),...facets[list].map(item=>new Option(`${item.name} (${item.count})`,item.name)));
          select.value=value;
        }
      }
      if(facets.total!==previous.facets.total)document.getElementById('library-count').firstChild.textContent=`${facets.total} reports in your library.`;
      libraryState={key,data,facets};
      if(anchor?.isConnected)window.scrollBy(0,anchor.getBoundingClientRect().top-anchorTop);
      else if(window.scrollY!==scrollY)window.scrollTo(window.scrollX,scrollY);
    } catch(error) {
      // Keep the last successful view during transient failures; retry next time.
    } finally {libraryRefreshInFlight=false;}
  }

  function uploadPage() {
    selectedFile=null;document.title='Upload report · Portal';
    main.innerHTML=heading('Something worth keeping.','Add an HTML report or a ZIP bundle with its assets.')+`<form id="upload-form" class="panel import-hint"><div class="drop-zone" id="drop-zone"><strong>Drop your report here</strong><p>HTML, HTM, XHTML or ZIP · up to 50 MB</p><input id="upload-file" type="file" accept=".html,.htm,.xhtml,.zip" aria-label="Report file"><p class="file-details" id="file-details"></p></div><div class="form-grid">
      <div class="field wide"><label for="upload-title">Title <span class="help">(optional; read from the report)</span></label><input id="upload-title" name="title" maxlength="500" placeholder="A useful piece of research"></div>
      <div class="field"><label for="upload-topic">Collection</label><input id="upload-topic" name="topic" value="General" maxlength="100" placeholder="e.g. Gift ideas"></div><div class="field"><label for="upload-source">Source</label><input id="upload-source" name="source" value="Manual upload" maxlength="100"></div>
      <div class="field"><label for="upload-date">Report date</label><input id="upload-date" type="date" name="date" value="${new Date().toLocaleDateString('en-CA')}"></div><div class="field"><label for="upload-tags">Tags</label><input id="upload-tags" name="tags" placeholder="Separate tags with commas"></div></div>
      <p class="help">A ZIP should contain <code>index.html</code> and any assets using relative links. Each upload creates a new private report.</p><div class="actions"><button class="primary-button" type="submit">Import report</button><a class="text-button" data-nav href="/">Back to library</a></div><div id="upload-status" class="sr-status" role="status"></div></form>`;
    const zone=document.getElementById('drop-zone');
    zone.addEventListener('click',event=>{if(event.target.id!=='upload-file')document.getElementById('upload-file').click();});
    zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('is-dragging');});
    zone.addEventListener('dragleave',()=>zone.classList.remove('is-dragging'));
    zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('is-dragging');chooseFile(e.dataTransfer.files[0]);});
  }
  function chooseFile(file) {selectedFile=file;document.getElementById('file-details').textContent=file?`${file.name} · ${(file.size/1024).toFixed(0)} KB`:'';}
  async function upload(event) {
    event.preventDefault();const form=event.target,status=document.getElementById('upload-status');
    status.classList.remove('error');
    if(!selectedFile){status.classList.add('error');status.textContent='Choose a report first.';return;}
    const body=new FormData(form);body.set('file',selectedFile);
    const button=form.querySelector('[type=submit]');button.disabled=true;status.textContent='Importing your report…';
    try {const report=await request('/reports',{method:'POST',body});toast('Report saved to your library.');navigate(report.reader_url);}
    catch(error){status.classList.add('error');status.textContent=error.message;button.disabled=false;}
  }

  async function readerPage(id) {
    const r=await request('/reports/'+encodeURIComponent(location.pathname.split('/')[2]),{signal:routeController.signal});if(id!==generation)return;
    currentReport=r;document.title=r.title+' · Portal';
    const available=await fetch(r.url,{method:'HEAD',signal:routeController.signal});
    if(id!==generation)return;
    if(!available.ok)throw new Error('This report’s files are unavailable. Check report storage, then try again.');
    main.innerHTML=`<div class="reader-toolbar"><a class="reader-backlink" data-nav href="${esc(libraryUrl)}">← Library</a><div class="reader-info"><h1>${esc(r.title)}</h1><p>${esc(r.topic)} · ${esc(r.source)} · ${esc(r.date)}</p></div><button class="text-button danger" data-delete-report>Delete</button><button class="primary-button" data-new-share>Share externally</button><a class="secondary-button" href="${esc(r.url)}" target="_blank" rel="noopener">Open ↗</a></div><iframe class="reader-frame" id="report-frame" title="${esc(r.title)}" sandbox="allow-scripts allow-forms allow-downloads allow-popups allow-modals" src="${esc(r.url)}"></iframe>`;
  }

  async function settingsPage(id) {
    settings=await request('/settings',{signal:routeController.signal});if(id!==generation)return;
    document.title='Settings · Portal';
    main.innerHTML=heading('Make yourself at home.','Manage agent access and optional folder imports.')+`<div class="settings-layout"><section class="panel"><h2>API access</h2><p>Use this key to submit reports from your agents and other computers. The API is available at <code>${esc(location.origin)}/api</code>.</p><div class="key-row"><input id="api-key" type="password" readonly autocomplete="off" aria-label="API key" value="${esc(settings.api_key)}"><button class="secondary-button" data-reveal>Show</button><button class="secondary-button" data-copy-key>Copy key</button><button class="secondary-button" data-rotate>Regenerate</button></div><p class="help">Regenerating immediately disconnects clients using the old key. <a href="/settings#api-guide" id="api-guide-link">Agent submission example ↓</a></p></section>
      <section class="panel"><h2>Folder imports</h2><p>Consume completed HTML files, ZIP bundles, or report folders from locations you choose. Successful imports remove the incoming submission; failures stay in place.</p><label class="toggle-row"><input type="checkbox" id="watching-enabled" ${settings.watching_enabled?'checked':''}>Enable folder watching</label><p class="help">Checks every 5 seconds; waits for files to remain unchanged for at least 10 seconds. For HTML with assets, put the entire submission in its own folder. Mounted NAS folders are reconciled through these scans.</p><button class="secondary-button" type="button" data-refresh-watchers>Refresh activity</button><div id="watchers">${settings.watchers.map(w=>`<div class="watch-row"><p class="watch-path">${esc(w.path)}</p><p class="watch-status">${!settings.watching_enabled?'Paused (watching is off)':w.enabled?'Enabled':'Paused'} · Last checked: ${w.last_checked?esc(date(w.last_checked)):'Not yet'}${w.last_import?' · Last import: '+esc(date(w.last_import)):''}</p>${w.error?`<p class="error">${esc(w.error)}</p>`:''}<div class="actions"><button class="secondary-button" data-watch-toggle="${w.id}" data-enabled="${!w.enabled}">${w.enabled?'Pause':'Enable'}</button><button class="text-button" data-watch-remove="${w.id}">Remove location</button></div></div>`).join('')||'<p class="help">No folders configured. API and manual uploads work independently.</p>'}</div>${watchActivity(settings.activity)}<form id="watch-form" class="watch-edit"><div class="field"><label for="watch-path">Add a folder inside the container</label><input id="watch-path" name="path" placeholder="/mnt/reports/inbox" required></div><div class="actions"><button class="secondary-button">Add location</button></div><p class="help">To change a location, remove it and add the new path. Existing reports are preserved.</p><p id="watch-error" class="error" role="alert"></p></form></section>
      <section class="panel"><h2>Storage</h2><p>Storage mounts are configured at deployment. The database and settings stay separate from report files.</p><p class="storage-line">Reports: ${esc(settings.storage)}/reports</p><p class="storage-line">Snapshots: ${esc(settings.storage)}/shares</p></section>
      <section class="panel" id="api-guide"><h2>Send a report from an agent</h2><p>Upload one report per request. Optional fields include title, source, topic, date, and comma-separated tags.</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;margin-top:18px">curl -X POST '${esc(location.origin)}/api/reports' \\\n  -H 'X-Api-Key: YOUR_API_KEY' \\\n  -F 'file=@report.zip' \\\n  -F 'topic=Research' \\\n  -F 'source=Agent' \\\n  -F 'tags=ideas,comparison'</pre><p class="help">The response includes a private reader link. Creating a public share is a separate action. API documentation is available at <code>/api/docs</code> with the same key.</p></section></div>`;
  }

  function watchActivity(activity=[]) {
    return '<div id="watch-activity"><h3>Recent imports</h3>'+ (activity.length?activity.map(a=>`<p>${esc(date(a.created_at))} · ${esc(a.outcome)} · ${esc(a.filename)} · <a data-nav href="/read/${esc(a.report_id)}">View report</a></p>`).join(''):'<p class="help">No imports yet.</p>')+'</div>';
  }

  async function sharesPage(id) {
    const data=await request('/shares',{signal:routeController.signal});if(id!==generation)return;
    document.title='Shared snapshots · Portal';
    main.innerHTML=heading('Share the useful part.','Independent snapshots. You choose when they change.')+`<section class="panel">${data.shares.length?data.shares.map(s=>{const expired=s.expires_at && s.expires_at*1000<=Date.now();return `<article class="shared-row"><div class="share-top"><h3>${esc(s.title)}</h3><span class="badge ${expired?'expired':''}">${expired?'Expired':s.password_protected?'Password protected':'Anyone with link'}</span></div><div class="share-meta"><span>Created ${esc(date(s.created_at))}</span><span>· Updated ${esc(date(s.updated_at))}</span><span>· Expires ${esc(date(s.expires_at))}</span></div><input class="share-url" aria-label="Share URL" readonly value="${esc(location.origin+s.url)}"><div class="actions"><button class="secondary-button" data-copy-share="${esc(s.url)}">Copy link</button><a class="secondary-button" href="${esc(s.url)}" target="_blank" rel="noopener">Open link ↗</a><button class="secondary-button" data-edit-share="${s.token}">Update snapshot / access</button><button class="text-button danger" data-revoke="${s.token}">Revoke</button></div></article>`;}).join(''):'<div class="state-panel"><h3>Nothing shared yet.</h3><p>Open a report and choose “Share externally” to create a snapshot.</p><a class="primary-button" href="/" data-nav>Browse your library</a></div>'}</section>`;
  }

  function shareForm(share=null) {
    const editing=Boolean(share);shareSelection=editing?share.report_id:currentReport.id;
    openDialog(dialogTop(editing?'Update shared snapshot':'Share externally')+`<p class="dialog-copy">${editing?'The existing URL stays the same. Choose a replacement report when you are ready to share it.':'Anyone with the link can read this snapshot, subject to your password and expiry settings. New reports will not change it automatically.'}</p><form id="share-form" data-token="${share?.token||''}">${editing?`<div class="field"><label for="replacement-search">Find a replacement report (optional)</label><input id="replacement-search" type="search" placeholder="Search your library…" autocomplete="off"></div><div class="report-picker" id="report-picker"></div><p class="help" id="replacement-status">Keeping the current report: ${esc(share.title)}</p>`:`<p class="share-report-title">${esc(currentReport.title)}</p>`}
      <div class="form-grid" style="margin-top:20px"><div class="field wide"><label for="share-days">${editing?'Change expiry':'Link expires'}</label><select id="share-days">${editing?'<option value="keep">Keep current expiry</option>':''}<option value="1">In 1 day</option><option value="7" ${editing?'':'selected'}>In 7 days</option><option value="30">In 30 days</option><option value="custom">Custom number of days</option><option value="never">Never</option></select></div><div class="field wide" id="custom-days-field" hidden><label for="custom-days">Days from now</label><input id="custom-days" type="number" min="0.01" max="3650" step="any" value="14"></div><div class="field wide"><label for="share-password">${editing?'Password change (optional)':'Password (optional)'}</label><input id="share-password" type="password" maxlength="256" autocomplete="new-password" placeholder="${editing?'Leave blank to keep current password':'Leave blank for link-only access'}"></div>${editing?'<label class="toggle-row wide"><input type="checkbox" id="remove-password">Remove existing password</label>':''}</div><div class="actions"><button type="submit" class="primary-button">${editing?'Update share':'Create snapshot link'}</button><button type="button" class="secondary-button" data-close>Cancel</button></div><div id="share-error" class="error" role="alert"></div></form>`);
  }

  async function saveShare(event) {
    event.preventDefault();const form=event.target,token=form.dataset.token;
    const body={report_id:shareSelection};const expiry=document.getElementById('share-days').value;
    if(expiry!=='keep')body.expires_in_days=expiry==='never'?null:expiry==='custom'?Number(document.getElementById('custom-days').value):Number(expiry);
    const password=document.getElementById('share-password').value;
    if(password || document.getElementById('remove-password')?.checked)body.password=document.getElementById('remove-password')?.checked?'':password;
    const button=form.querySelector('[type=submit]');button.disabled=true;
    try {
      const share=await request('/shares'+(token?'/'+token:''),{method:token?'PATCH':'POST',body});
      if(location.pathname==='/shares')await sharesPage(generation);
      openDialog(dialogTop(token?'Share updated':'Your snapshot is ready')+`<p class="dialog-copy">${token?'The existing link now uses your selected snapshot and access settings.':'Your report remains private in the library. This link opens its independent snapshot.'}</p><input class="share-url" aria-label="Created share URL" readonly value="${esc(location.origin+share.url)}"><div class="actions"><button class="primary-button" data-copy-share="${esc(share.url)}">Copy link</button><button class="secondary-button" data-close>Done</button><a href="/shares" class="text-button" data-nav>Manage shares</a></div>`);
    }catch(error){document.getElementById('share-error').textContent=error.message;button.disabled=false;}
  }

  async function copy(text) {try{await navigator.clipboard.writeText(text);toast('Copied.');}catch(_){openDialog(dialogTop('Copy this value')+`<input class="share-url" aria-label="Value to copy" readonly value="${esc(text)}"><p class="help">Select the value and copy it using your keyboard.</p>`);dialog.querySelector('input').select();}}

  document.addEventListener('click',async event=>{
    const target=event.target.closest('a,button');if(!target)return;
    if(target.matches('a[data-nav]') && event.button===0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey){event.preventDefault();navigate(target.getAttribute('href'));return;}
    try {
      if(target.hasAttribute('data-close'))closeDialog();
      if(target.hasAttribute('data-retry'))render();
      if(target.hasAttribute('data-reset'))navigate('/');
      if(target.dataset.page){const p=params();p.set('page',target.dataset.page);navigate('/?'+p);}
      if(target.id==='filters-toggle'){const expanded=target.getAttribute('aria-expanded')!=='true';filtersOpen=expanded;target.setAttribute('aria-expanded',expanded);document.getElementById('filter-panel').classList.toggle('is-open',expanded);}
      if(target.hasAttribute('data-new-share'))shareForm();
      if(target.hasAttribute('data-delete-report'))openDialog(dialogTop('Delete this report?')+'<p class="dialog-copy">This removes the report and its files from your library. Existing shared snapshots remain available.</p><div class="actions"><button class="primary-button" data-confirm-delete>Delete report</button><button class="secondary-button" data-close>Cancel</button></div>');
      if(target.hasAttribute('data-confirm-delete')){await request('/reports/'+currentReport.id,{method:'DELETE'});toast('Report deleted. Shared snapshots are preserved.');navigate(libraryUrl);}
      if(target.hasAttribute('data-refresh-watchers')){await settingsPage(generation);toast('Watcher activity refreshed.');}
      if(target.hasAttribute('data-reveal')){const input=document.getElementById('api-key');input.type=input.type==='password'?'text':'password';target.textContent=input.type==='password'?'Show':'Hide';}
      if(target.hasAttribute('data-copy-key'))await copy(settings.api_key);
      if(target.hasAttribute('data-rotate'))openDialog(dialogTop('Regenerate API key?')+'<p class="dialog-copy">Every client using the current key will stop working until you update it. Your reports and share links are preserved.</p><div class="actions"><button class="primary-button" data-confirm-rotate>Regenerate key</button><button class="secondary-button" data-close>Cancel</button></div>');
      if(target.hasAttribute('data-confirm-rotate')){await request('/settings/api-key',{method:'POST'});toast('API key regenerated.');render();}
      if(target.dataset.watchToggle){await request('/watchers/'+target.dataset.watchToggle,{method:'PATCH',body:{enabled:target.dataset.enabled==='true'}});render();}
      if(target.dataset.watchRemove){await request('/watchers/'+target.dataset.watchRemove,{method:'DELETE'});toast('Location removed. Imported reports are preserved.');render();}
      if(target.dataset.copyShare)await copy(location.origin+target.dataset.copyShare);
      if(target.dataset.editShare){const {shares}=await request('/shares');shareForm(shares.find(s=>s.token===target.dataset.editShare));}
      if(target.dataset.revoke)openDialog(dialogTop('Revoke this share?')+`<p class="dialog-copy">The link will stop working immediately. Your library report is preserved.</p><div class="actions"><button class="primary-button" data-confirm-revoke="${esc(target.dataset.revoke)}">Revoke link</button><button class="secondary-button" data-close>Cancel</button></div>`);
      if(target.dataset.confirmRevoke){await request('/shares/'+target.dataset.confirmRevoke,{method:'DELETE'});toast('Share revoked.');render();}
      if(target.dataset.chooseReport){shareSelection=target.dataset.chooseReport;for(const button of dialog.querySelectorAll('[data-choose-report]'))button.setAttribute('aria-pressed',button===target);document.getElementById('replacement-status').textContent='Replacement: '+target.textContent;}
    } catch(error){toast(error.message);}
  });
  document.addEventListener('submit',async event=>{
    if(event.target.id==='upload-form')return upload(event);
    if(event.target.id==='share-form')return saveShare(event);
    if(event.target.id==='search-form'){event.preventDefault();filters({q:document.getElementById('search').value});}
    if(event.target.id==='watch-form'){event.preventDefault();try{await request('/watchers',{method:'POST',body:{path:document.getElementById('watch-path').value}});render();}catch(error){document.getElementById('watch-error').textContent=error.message;}}
  });
  document.addEventListener('change',async event=>{
    const el=event.target;
    if(el.dataset.filter)filters({[el.dataset.filter]:el.value});
    if(el.id==='upload-file')chooseFile(el.files[0]);
    if(el.id==='share-days')document.getElementById('custom-days-field').hidden=el.value!=='custom';
    if(el.id==='watching-enabled'){try{await request('/settings',{method:'PATCH',body:{watching_enabled:el.checked}});toast(el.checked?'Folder watching enabled.':'Folder watching disabled.');await settingsPage(generation);}catch(error){el.checked=!el.checked;toast(error.message);}}
  });
  let pickerRequest=0;
  document.addEventListener('input',event=>{
    if(event.target.id==='replacement-search'){
      clearTimeout(searchTimer);const value=event.target.value,seq=++pickerRequest;
      searchTimer=setTimeout(async()=>{try{const data=await request('/reports?limit=30&q='+encodeURIComponent(value));if(seq!==pickerRequest || !document.getElementById('report-picker'))return;document.getElementById('report-picker').innerHTML=data.reports.map(r=>`<button type="button" class="report-choice" data-choose-report="${r.id}" aria-pressed="${r.id===shareSelection}">${esc(r.title)} · ${esc(r.date)}</button>`).join('')||'<p class="help">No matching reports.</p>';}catch(error){toast(error.message);}},250);
    }
  });
  document.addEventListener('keydown',event=>{if(event.key==='/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName) && document.getElementById('search')){event.preventDefault();document.getElementById('search').focus();}});
  window.addEventListener('popstate',()=>{if(renderedUrl!==location.pathname+location.search)render(true);});
  setInterval(refreshLibrary,30000);
  render();
})();
