(() => {
  if (!document.body) return null;
  const cache = window.__eveFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => e.type!=='hidden';
  // Password values never leave the page; the model only learns whether the field is filled.
  const secret = e => e.type==='password';
  const valueOf = e => secret(e) ? (e.value ? 'filled' : '') : e.value;
  const toggle = e => e.tagName==='INPUT' && ['checkbox','radio'].includes(e.type);
  // Styled checkboxes are often transparent inputs over a drawn box. They still take clicks.
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:!toggle(e),checkVisibilityCSS:true});
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel','password','date','time','datetime-local','month','week','color']
        .includes(e.type)) return 'textbox';
      if (e.type==='range') return 'slider';
    }
    return null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),valueOf(e),e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),valueOf(e)??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  // Same-origin frames are read like the page itself, with their offset. Other frames can be opened on their own.
  const frames=[], embedded=[];
  const collect=(doc,dx,dy,clip,depth)=>{
    frames.push({doc,dx,dy,clip});
    if (depth>2) return;
    for (const f of doc.querySelectorAll('iframe,frame')) {
      if (!visible(f)) continue;
      const r=f.getBoundingClientRect();
      if (r.width<40 || r.height<40) continue;
      const fx=dx+r.x+f.clientLeft, fy=dy+r.y+f.clientTop;
      const inner={l:Math.max(clip.l,fx),t:Math.max(clip.t,fy),
        r:Math.min(clip.r,fx+f.clientWidth),b:Math.min(clip.b,fy+f.clientHeight)};
      if (inner.r<=inner.l || inner.b<=inner.t) continue;
      let child=null;
      try { child=f.contentDocument; } catch { child=null; }
      if (child?.body) collect(child,fx,fy,inner,depth+1);
      else if (/^https?:/.test(f.src) && r.width>=150 && r.height>=100)
        embedded.push({f,src:f.src,label:f.title||f.getAttribute('aria-label')||new URL(f.src).hostname});
    }
  };
  // Controls up to one screen below the fold are offered too; the executor scrolls them into view first.
  collect(document,0,0,{l:0,t:0,r:innerWidth,b:innerHeight*2},0);
  // Open shadow roots (web components) hold controls and text the document query cannot see.
  const roots=root=>{
    const out=[root];
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) out.push(...roots(el.shadowRoot));
    return out;
  };
  const actions=[];
  for (const {doc,dx,dy,clip} of frames) for (const root of roots(doc)) for (const e of root.querySelectorAll(selector)) {
    if (!safe(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    if (e.tagName==='INPUT' && e.type==='file') {
      // Often hidden behind a styled button; it is set through CDP, never clicked.
      actions.push({node:identity(e),role:'file',kind:'file',label:name(e)||'File',
        value:[...(e.files||[])].map(f=>f.name).join(', ')||'no file chosen',rect:{x:0,y:0,w:0,h:0}});
      continue;
    }
    if (!visible(e)) continue;
    const box=e.getBoundingClientRect(), rname=role(e);
    const r={x:dx+box.x,y:dy+box.y,width:box.width,height:box.height}, x=r.x+r.width/2, y=r.y+r.height/2;
    if (!rname || r.width<=0 || r.height<=0 || x<clip.l || y<clip.t || x>=clip.r || y>=clip.b) continue;
    // A cell or menu item wrapping its own control (a calendar day's checkbox, a menu's link) is offered once,
    // as that control.
    // Only when the inner control carries the container's own leading label: a suggestion with a small
    // "nearby airports" button inside is still its own choice.
    if (['gridcell','menuitem','treeitem','tab','option'].includes(rname)) {
      const own=name(e).replace(/\s+/g,' ').trim();
      if ([...e.querySelectorAll(selector)].some(c=>{ const n=name(c).replace(/\s+/g,' ').trim(); return n && own.startsWith(n); }))
        continue;
    }
    // An unnamed checkbox (a to-do toggle, say) takes its row's text so the model can tell rows apart.
    const row=toggle(e) && !name(e) ? e.closest('li,tr,[role="row"],label')?.innerText.trim().slice(0,80) : '';
    const base={node:identity(e),role:rname,label:(name(e)||row||rname).replace(/\s+/g,' ').trim(),
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='INPUT' && !['text','search','checkbox','radio','submit','button'].includes(e.type))
      base.input_type=e.type;
    if (e.type==='range') Object.assign(base,{min:e.min||'0',max:e.max||'100',step:e.step||'1'});
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton','slider'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(valueOf(e)) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      actions.push({...base,kind:editable?'fill':'click',value});
      if (editable) actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  // Drag-and-drop: things that can be dragged, and places that take a drop.
  const sources=new Set(), drops=new Set();
  for (const {doc,dx,dy,clip} of frames) {
    const pick=(query,set,kind,role)=>{
      for (const e of doc.querySelectorAll(query)) {
        if (set.size>=30 || !visible(e)) continue;
        const box=e.getBoundingClientRect(), x=dx+box.x+box.width/2, y=dy+box.y+box.height/2;
        if (!box.width || !box.height || x<clip.l || y<clip.t || x>=clip.r || y>=clip.b) continue;
        const id=identity(e);
        if (set.has(id)) continue;
        set.add(id);
        const label=(name(e)||e.innerText||e.id||'').replace(/\s+/g,' ').trim().slice(0,60)||(role+' '+set.size);
        actions.push({node:id,role,kind,label,value:'',rect:{x:dx+box.x,y:dy+box.y,w:box.width,h:box.height}});
      }
    };
    pick('[draggable="true"],.ui-draggable,[class*="draggable" i],[class*="sortable" i] > *,[aria-grabbed]',
      sources,'drag','draggable');
    pick('.ui-droppable,[class*="droppable" i],[class*="dropzone" i],[class*="drop-zone" i],[id*="drop" i],' +
      '[aria-dropeffect],[draggable="true"]',drops,'drop','drop zone');
  }
  if (!sources.size) for (let i=actions.length-1; i>=0; i--) if (actions[i].kind==='drop') actions.splice(i,1);
  // Menus and captions that only appear under the pointer.
  const hovers=new Set();
  for (const {doc,dx,dy,clip} of frames) {
    const candidates=doc.querySelectorAll('[aria-haspopup]:not([aria-haspopup="false"]),nav li,' +
      '[role="menubar"] [role="menuitem"],figure,.figure,.dropdown,.has-dropdown,.menu-item-has-children');
    for (const e of candidates) {
      if (hovers.size>=30 || !visible(e)) continue;
      const hidden=[...e.querySelectorAll('a,button,ul,figcaption,.figcaption,[role="menu"]')]
        .some(c=>!c.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}));
      if (!hidden) continue;
      const box=e.getBoundingClientRect(), x=dx+box.x+box.width/2, y=dy+box.y+box.height/2;
      if (box.width<=0 || box.height<=0 || x<clip.l || y<clip.t || x>=clip.r || y>=clip.b) continue;
      const id=identity(e);
      if (hovers.has(id)) continue;
      hovers.add(id);
      const label=(name(e.querySelector('a,button,img')||e)||e.innerText||'').replace(/\s+/g,' ').trim().slice(0,60);
      actions.push({node:id,role:'menu',kind:'hover',label:label||'Element '+hovers.size,value:'',
        rect:{x:dx+box.x,y:dy+box.y,w:box.width,h:box.height}});
    }
  }
  // Things that are clickable only by script and style (sortable headers, cards): sample the screen and keep
  // the outermost element under each point whose cursor turns into a pointer.
  const known=new Set(actions.map(a=>a.node));
  const interactive=e=>e.closest(selector);
  const offer=(e,dx,dy,clip)=>{
    while (e.parentElement && e.parentElement!==e.ownerDocument.body &&
           getComputedStyle(e.parentElement).cursor==='pointer' && !interactive(e.parentElement)) e=e.parentElement;
    if (interactive(e) || e.querySelector(selector) || known.has(identity(e)) || !visible(e)) return;
    const box=e.getBoundingClientRect(), x=dx+box.x+box.width/2, y=dy+box.y+box.height/2;
    if (!box.width || !box.height || x<clip.l || y<clip.t || x>=clip.r || y>=clip.b) return;
    const label=(name(e)||e.innerText||e.getAttribute('title')||'').replace(/\s+/g,' ').trim();
    if (!label || label.length>80 || box.width*box.height>innerWidth*innerHeight/4) return;
    known.add(identity(e));
    actions.push({node:identity(e),role:e.tagName==='TH' ? 'columnheader' : 'clickable',kind:'click',label,value:'',
      rect:{x:dx+box.x,y:dy+box.y,w:box.width,h:box.height}});
  };
  // Sample the screen for elements whose cursor turns into a pointer.
  for (let py=40; py<innerHeight; py+=56) for (let px=24; px<innerWidth; px+=64) {
    const e=document.elementFromPoint(px,py);
    if (e && e.tagName!=='IFRAME' && !interactive(e) && getComputedStyle(e).cursor==='pointer')
      offer(e,0,0,{l:0,t:0,r:innerWidth,b:innerHeight});
  }
  // Script-wired controls that look like nothing in particular: column headers, links without href,
  // elements carrying click handlers. Searched in every frame and shadow root.
  const wired='th,a:not([href]),[onclick],[data-handler],[data-action],[data-event],[role="columnheader"],[aria-sort],' +
    '[tabindex="0"],[jsaction]';
  for (const {doc,dx,dy,clip} of frames) for (const root of roots(doc))
    for (const e of root.querySelectorAll(wired)) if (!interactive(e)) offer(e,dx,dy,clip);
  // A calendar day ("15") says which month it belongs to.
  const months=/(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}/i;
  for (const a of actions) {
    if (!/^\d{1,2}$/.test(a.label)) continue;
    const table=cache.nodes.get(a.node)?.closest('table');
    const scope=table?.closest('[class*="calendar"],[class*="datepicker"],[class*="picker"],[role="dialog"]') ||
      table?.parentElement;
    const month=(table?.caption?.innerText||'').match(months) || (scope?.innerText||'').slice(0,400).match(months);
    if (month) a.label+=' '+month[0];
  }
  // Identical labels ("Add to cart", "Reply", "Toggle Todo") get their row's text so each target is distinct.
  // Counted per operation: the same card as a drag source and a drop zone is not ambiguous.
  const key=a=>a.kind+'|'+a.label, counts={};
  for (const a of actions) counts[key(a)]=(counts[key(a)]||0)+1;
  for (const a of actions) {
    if (counts[key(a)]<2) continue;
    const row=cache.nodes.get(a.node)?.closest('li,tr,[role="row"],[role="listitem"],article');
    const context=row?.innerText.replace(/\s+/g,' ').trim().slice(0,90);
    if (context && context!==a.label) a.label+=' · '+context;
  }
  // Still identical (three "User Avatar" images): number them in page order.
  const left={}, seen={};
  for (const a of actions) left[key(a)]=(left[key(a)]||0)+1;
  for (const a of actions) if (left[key(a)]>1 && a.kind!=='select') {
    const k=key(a);
    seen[k]=(seen[k]||0)+1;
    a.label+=` (${seen[k]} of ${left[k]})`;
  }
  const words=[]; let node,length=0;
  for (const {doc,dx,dy,clip} of frames) for (const root of roots(doc)) {
    const walker=doc.createTreeWalker(root===doc ? doc.body : root,NodeFilter.SHOW_TEXT), range=doc.createRange();
    while ((node=walker.nextNode()) && length<6000) {
      const value=node.textContent.trim(), parent=node.parentElement;
      if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
      range.selectNodeContents(node); const r=range.getBoundingClientRect();
      if (r.width>0 && r.height>0 && dy+r.bottom>clip.t && dy+r.top<clip.b && dx+r.right>clip.l && dx+r.left<clip.r) {
        words.push(value); length+=value.length;
      }
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6]];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  embedded.slice(0,3).forEach((x,i)=>actions.push({id:'open_frame_'+(i+1),kind:'frame',node:identity(x.f),
    label:'Open the embedded '+x.label+' page by itself',value:x.src}));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    pdf:document.contentType==='application/pdf',
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
