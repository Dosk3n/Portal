// Apply the saved theme before styles paint, including when storage is unavailable.
(() => {
  'use strict';
  const root=document.documentElement;
  const system=window.matchMedia('(prefers-color-scheme: dark)');
  let saved;
  try { saved=localStorage.getItem('portal-theme'); } catch (_) {}
  function apply(theme) {
    const dark=theme==='dark';
    root.dataset.theme=dark?'dark':'light';
    const toggle=document.getElementById('theme-toggle');
    if(toggle){toggle.setAttribute('aria-checked',String(dark));toggle.title=dark?'Switch to light mode':'Switch to dark mode';}
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content',dark?'#15211d':'#244d40');
  }
  apply(saved==='dark'||saved==='light'?saved:system.matches?'dark':'light');
  system.addEventListener('change',()=>{if(saved!=='dark'&&saved!=='light')apply(system.matches?'dark':'light');});
  window.addEventListener('storage',event=>{
    if(event.key==='portal-theme'||event.key===null){saved=event.newValue;apply(saved==='dark'||saved==='light'?saved:system.matches?'dark':'light');}
  });
  document.addEventListener('DOMContentLoaded',()=>{
    apply(root.dataset.theme);
    document.getElementById('theme-toggle').addEventListener('click',()=>{
      saved=root.dataset.theme==='dark'?'light':'dark';
      apply(saved);
      try { localStorage.setItem('portal-theme',saved); } catch (_) {}
    });
  });
})();
