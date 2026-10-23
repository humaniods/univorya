/* Univorya client: dependency-free SPA. All saved project data comes from /api. */
const state = {
  user:null, meta:null, projects:[], pagination:null,
  filter:{q:'',category:'All',stage:'All',sort:'trending'},
  loading:false, route:'/', pendingAfterAuth:'/dashboard', total:0,
  pickedScreens:[], pickedCover:null, existingMedia:[], keepDraft:false, currentProject:null, lightbox:-1
};
const $ = (s,root=document) => root.querySelector(s);
const $$ = (s,root=document) => [...root.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// A mistyped %xx in a URL would throw here and leave the visitor on a blank page.
const dec = v => { try { return decodeURIComponent(v); } catch (e) { return v; } };
const reduces = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const kb = bytes => bytes >= 1024*1024 ? (bytes/1024/1024).toFixed(1)+' MB' : Math.max(1,Math.round(bytes/1024))+' KB';
const icons = {
  spark:'<path d="m12 3 1.9 6.1L20 11l-6.1 1.9L12 19l-1.9-6.1L4 11l6.1-1.9L12 3Z"/><path d="m19 18 .7 2.3L22 21l-2.3.7L19 24l-.7-2.3L16 21l2.3-.7L19 18Z"/>',
  arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>', chevron:'<path d="m9 18 6-6-6-6"/>', down:'<path d="m6 9 6 6 6-6"/>',
  plus:'<path d="M12 5v14M5 12h14"/>', search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  globe:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c6 5 6 13 0 18M12 3c-6 5-6 13 0 18"/>',
  code:'<path d="m8 8-4 4 4 4m8-8 4 4-4 4M14 4l-4 16"/>',
  rocket:'<path d="M4 15c-1 0-2 3-2 5l5-1 3-4M9 14l1 1c6-1 10-6 11-12-6 1-11 5-12 11ZM5 7c2-3 5-4 9-3M8 18c0 2-2 4-5 4"/><circle cx="15" cy="9" r="1.6"/>',
  grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  heart:'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
  external:'<path d="M14 4h6v6M10 14 20 4M20 13v7H4V4h7"/>',
  github:'<path d="M9 19c-4.4 1.4-4.4-2.5-6-3m12 6v-3.3a3 3 0 0 0-.8-2.4c3-.4 6.2-1.5 6.2-6.7A5.3 5.3 0 0 0 19 6a4.8 4.8 0 0 0-.1-3s-1.1-.3-3.3 1.5a11.2 11.2 0 0 0-6 0C7.4 2.7 6.3 3 6.3 3A4.8 4.8 0 0 0 6.2 6a5.3 5.3 0 0 0-1.4 3.6c0 5.2 3.2 6.3 6.2 6.7a3 3 0 0 0-.8 2.4V22"/>',
  user:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  upload:'<path d="M12 16V4m-5 5 5-5 5 5M4 16v4h16v-4"/>',
  layers:'<path d="m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5"/>',
  circle:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  alert:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/>',
  close:'<path d="M18 6 6 18M6 6l12 12"/>',
  menu:'<path d="M4 7h16M4 12h16M4 17h16"/>',
  filter:'<path d="M4 7h16M7 12h10M10 17h4"/>',
  layout:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M9 9v12"/>',
  phone:'<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18h2"/>',
  cpu:'<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9zM9 1v4m6-4v4M9 19v4m6-4v4M1 9h4M1 15h4m14-6h4m-4 6h4"/>',
  terminal:'<path d="m4 7 5 5-5 5M12 17h8"/>',
  bulb:'<path d="M9 18h6M10 22h4M9 15c-2-2-4-3-4-7a7 7 0 0 1 14 0c0 4-2 5-4 7v3H9v-3Z"/>',
  palette:'<path d="M12 3a9 9 0 1 0 0 18h1.4a2 2 0 0 0 1.4-3.4c-.7-.7-.4-1.6.7-1.6H18a3 3 0 0 0 3-3 10 10 0 0 0-9-10Z"/><circle cx="8" cy="10" r="1"/><circle cx="12" cy="7" r="1"/><circle cx="16" cy="10" r="1"/>',
  star:'<path d="m12 2 3 6.5 7 .8-5.2 5 1.4 7L12 18l-6.2 3.3 1.4-7-5.2-5 7-.8L12 2Z"/>',
  link:'<path d="M10 13a5 5 0 0 0 7 .5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/>',
  trash:'<path d="M4 7h16M9 7V4h6v3m4 0-1 14H6L5 7m5 4v6m4-6v6"/>',
  logout:'<path d="M10 4H4v16h6M14 8l4 4-4 4M8 12h10"/>',
  users:'<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M17 5a3 3 0 0 1 0 6M20 20v-2a6 6 0 0 0-3-5"/>',
  eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
  image:'<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="m4 17 5-5 4 4 3-2 4 4"/>'
};
const ic = (name, size=18) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]||icons.spark}</svg>`;

/* ---------------------------------------------------------------- transport */
async function api(url, options={}) {
  const opts={credentials:'same-origin',...options};
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body!=='string') {opts.headers={...opts.headers,'Content-Type':'application/json'};opts.body=JSON.stringify(opts.body);}
  let res;
  try {res=await fetch(url,opts);}
  catch {const e=new Error("We couldn't reach Univorya. Check your connection and try again.");e.status=0;throw e;}
  const data=await res.json().catch(()=>({detail:'Unexpected server response'}));
  if (!res.ok) {const e=new Error(typeof data.detail==='string' ? data.detail : 'Please check the form and try again');e.status=res.status;throw e;}
  return data;
}

/* --------------------------------------------------------------------- toasts */
const toasts = new Map();
function toast(msg,kind='success') {
  const holder=$('#toast-container');if (!holder)return;
  const key=kind+'|'+msg, previous=toasts.get(key);
  if (previous) {clearTimeout(previous.timer);previous.el.remove();toasts.delete(key);}
  while (toasts.size >= 3) {const [oldest]=toasts.keys();const item=toasts.get(oldest);item.el.remove();toasts.delete(oldest);}
  const el=document.createElement('div');
  el.className=`toast ${kind}`;el.setAttribute('role',kind==='error'?'alert':'status');
  el.innerHTML=`${ic(kind==='success'?'circle':'alert',18)} <span>${esc(msg)}</span>`;
  holder.append(el);
  const timer=setTimeout(()=>{el.classList.add('toast-out');setTimeout(()=>{el.remove();toasts.delete(key);},320);},4800);
  toasts.set(key,{el,timer});
}

/* ---------------------------------------------------------------------- router */
function navigate(path) {
  const target=new URL(path,location.origin);
  if (target.pathname.endsWith('/') && target.pathname!=='/') target.pathname=target.pathname.replace(/\/+$/,'');
  const url=target.pathname+target.search;
  if(location.pathname+location.search!==url)history.pushState({},'',url);
  state.route=target.pathname;render();window.scrollTo({top:0,behavior:'instant'});
}
function filterSearch() {
  const p=new URLSearchParams();
  if(state.filter.q)p.set('q',state.filter.q);
  if(state.filter.category!=='All')p.set('category',state.filter.category);
  if(state.filter.stage!=='All')p.set('stage',state.filter.stage);
  if(state.filter.sort!=='trending')p.set('sort',state.filter.sort);
  const s=p.toString();return s?'?'+s:'';
}
function readFiltersFromUrl() {
  const p=new URLSearchParams(location.search), cats=state.meta?state.meta.categories:[];
  const category=p.get('category')||'All', stage=p.get('stage')||'All', sort=p.get('sort')||'trending';
  state.filter={q:(p.get('q')||'').slice(0,100),
    category:cats.includes(category)?category:'All',
    stage:state.meta&&state.meta.stages.includes(stage)?stage:'All',
    sort:['trending','newest','popular'].includes(sort)?sort:'trending'};
}
function writeFiltersToUrl(push=false) {
  if(state.route!=='/' && state.route!=='/explore')return;
  const url=state.route+filterSearch();
  if(location.pathname+location.search===url)return;
  history.pushState({},'',url);
  if(!push)history.replaceState({},'',url);
}
function formatDate(iso) {return new Date(iso).toLocaleDateString('en-US',{month:'short',year:'numeric'});}
function usernameAvatar(user,size='normal') {const initial=(user?.name || '?').trim().charAt(0).toUpperCase();return `<span class="avatar ${size==='small'?'avatar-sm':''}" style="--avatar:${esc(user?.avatar_color || '#865df5')}" aria-hidden="true">${esc(initial)}</span>`;}
function categoryIcon(c) {return ({'Web App':'globe','Mobile App':'phone','AI & ML':'cpu','Dev Tool':'terminal','Open Source':'code','Design':'palette','Idea':'bulb','Other':'layers'})[c]||'layers';}

function art(project,large=false) {
  const tint=esc(project.cover_style||'violet');
  const shot=(project.media||[]).find(m=>m.kind==='screenshot');
  const image=project.cover_image || (shot&&shot.file);
  if (image) {
    const meta=project.cover_image?{width:0,height:0,animated:false}:shot;
    return `<div class="cover-art ${large?'large-art':''} has-photo"><img src="${esc(image)}" alt="${esc(project.title)} cover image" loading="lazy" data-full="${esc(image)}" data-caption="${esc(project.title)}"/><div class="photo-sheen" aria-hidden="true"></div>${meta&&meta.animated?'<span class="shot-flag">live</span>':''}</div>`;
  }
  const word=esc(project.title).split(' ')[0];
  const glyph=categoryIcon(project.category);
  return `<div class="cover-art art-${tint} ${large?'large-art':''}"><div class="art-glow"></div><div class="art-grid"></div><div class="art-float art-float-one">${ic(glyph,large?32:22)}</div><div class="art-app"><div class="art-top"><i></i><i></i><i></i><span>${esc(project.category)}</span></div><div class="art-body"><div class="art-logo">${ic('spark',large?27:20)} <b>${word}</b></div><div class="art-lines"><span></span><span></span><span></span></div><div class="art-cols"><i></i><i></i><i></i></div></div></div><div class="art-orbit"></div></div>`;
}

function header() {
  const user=state.user;
  return `<header class="header"><div class="container nav-wrap"><a class="brand" href="/" data-link aria-label="Univorya home"><img class="brand-wordmark" src="/logo.svg" alt="Univorya"/></a>
 <nav class="nav-links" id="primary-nav" aria-label="Primary"><a data-link href="/explore" class="${state.route==='/explore'?'active':''}">Explore</a><a href="/explore?category=AI%20%26%20ML" data-action="category-shortcut" data-category="AI & ML">AI & ML</a><a href="/explore?category=Dev%20Tool" data-action="category-shortcut" data-category="Dev Tool">Dev tools</a>${user?`<a data-link href="/dashboard">Dashboard</a><a data-link href="/makers/${encodeURIComponent(user.username)}">My profile</a><button data-action="logout" class="nav-signout">${ic('logout',16)} Log out</button>`:`<a data-link href="/join">Create account</a>`}</nav>
 <div class="nav-right">${user?`<a href="/dashboard" data-link class="my-profile">${usernameAvatar(user,'small')}<span>${esc(user.name.split(' ')[0])}</span></a>`:`<a href="/login" data-link class="login-link">Log in</a>`}<a href="/submit" data-link class="btn btn-primary nav-submit">${ic('plus',17)} <span>Share your work</span></a><button class="mobile-menu icon-button" data-action="menu" aria-label="Open menu" aria-controls="primary-nav" aria-expanded="false">${ic('menu',21)}</button></div></div></header>`;
}
function footer() {return `<footer class="footer"><div class="container footer-inside"><div><div class="brand footer-brand"><img class="brand-wordmark" src="/logo.svg" alt="Univorya"/></div><p>A little corner of the internet for people who make things.</p></div><div class="footer-end"><span>Crafted for curious minds ✦</span><span>Build, share, inspire.</span></div></div></footer>`;}
function frame(body,cls='') {$('#app').innerHTML=`<a class="skip-link" href="#main-content">Skip to content</a>`+header()+`<main id="main-content" tabindex="-1" class="${cls}">${body}</main>`+footer();document.title='Univorya — Where what you build gets discovered.';closeDrawer();}
function hero() {return `<section class="hero"><div class="container hero-grid"><div class="hero-copy"><div class="eyebrow"><span class="online-pulse"></span> THE HOME FOR PEOPLE WHO BUILD <span class="eyebrow-star">✳</span></div><h1>Great things<br/>deserve to be <span class="gradient-word">seen<span class="dot">.</span></span></h1><p class="hero-lede">A home for every idea, side project, and big launch. Share what you're building, discover inspiring work, and find your people.</p><div class="hero-buttons"><a href="/explore" data-link class="btn btn-primary btn-large">Explore projects ${ic('arrow',19)}</a><a href="/submit" data-link class="btn btn-ghost btn-large">Share your creation ${ic('plus',18)}</a></div><div class="hero-proof"><div class="proof-avatars"><span>R</span><span>A</span><span>M</span><span>J</span></div><div><strong>Ideas are better out in the world.</strong><small>From first sketches to full-fledged products</small></div></div></div><div class="hero-visual" aria-hidden="true"><div class="hero-halo"></div><div class="hero-orbit orbit-one"></div><div class="hero-orbit orbit-two"></div><div class="visual-card visual-card-main"><div class="visual-card-top"><span class="visual-dots"><i></i><i></i><i></i></span><span class="visual-badge">✦ Featured project</span></div><div class="visual-inner"><div class="visual-logo">${ic('spark',38)}</div><p class="visual-title">Made by makers.<br/>Built for everyone.</p><div class="visual-lines"><i></i><i></i></div><div class="visual-tiles"><span>✺</span><span>◈</span><span>✳</span></div></div><div class="visual-card-bottom">${ic('globe',15)} univorya / discover <span>${ic('arrow',16)}</span></div></div><div class="visual-float visual-float-top"><span class="visual-float-ico pink-ico">${ic('rocket',18)}</span><div><b>Just shipped!</b><small>Another idea goes live</small></div></div><div class="visual-float visual-float-bottom"><span class="visual-float-ico green-ico">${ic('heart',18)}</span><div><b>Made with passion</b><small>By independent creators</small></div></div><div class="visual-spark s-a">✦</div><div class="visual-spark s-b">✳</div></div></div></section>`;}
function categories() {const cats=['Web App','AI & ML','Mobile App','Dev Tool','Open Source','Design','Idea'];return `<section class="category-strip"><div class="container"><div class="section-kicker">EXPLORE BY INTEREST</div><div class="category-cards">${cats.map((c,i)=>`<button class="category-card ${state.filter.category===c?'cat-active':''}" style="--i:${i}" data-action="pick-category" data-category="${esc(c)}"><span class="category-icon">${ic(categoryIcon(c),20)}</span><span>${esc(c)}</span>${ic('chevron',15)}</button>`).join('')}</div></div></section>`;}
function projectCard(p,i=0) {
  const shots=(p.media||[]).filter(m=>m.kind==='screenshot').length;
  return `<article class="project-card reveal" style="--i:${i%24}"><a href="/p/${encodeURIComponent(p.slug)}" data-link class="project-cover-link" aria-label="View ${esc(p.title)}">${art(p)}<span class="cover-top-tag">${ic(categoryIcon(p.category),13)} ${esc(p.category)}</span>${p.is_demo?'<span class="demo-tag">SAMPLE</span>':''}${shots?`<span class="cover-shot-tag">${ic('image',12)} ${shots}</span>`:''}<span class="cover-open">${ic('arrow',18)}</span></a><div class="project-info"><div class="project-info-top"><span class="stage stage-${p.stage.toLowerCase().replaceAll(' ','-')}"><i></i>${esc(p.stage)}</span><span class="project-like-count">${ic('heart',14)} ${p.likes}</span></div><a href="/p/${encodeURIComponent(p.slug)}" data-link class="project-title">${esc(p.title)}</a><p class="project-tagline">${esc(p.tagline)}</p><div class="tech-list">${p.tech.slice(0,3).map(t=>`<span>${esc(t)}</span>`).join('')}</div><div class="project-bottom"><a href="/makers/${encodeURIComponent(p.creator.username)}" data-link class="creator">${usernameAvatar(p.creator,'small')}<span>${esc(p.creator.name)}</span></a><span class="tiny-date">${formatDate(p.created_at)}</span></div></div></article>`;
}
function catalog() {const isHome=state.route==='/';return `<section class="catalog-section" id="discover"><div class="container"><div class="catalog-heading"><div><div class="section-kicker"><span class="tiny-star">✦</span> CURATED FOR THE CURIOUS</div><h2>${isHome?'Explore the <em>extraordinary.</em>':'Discover what’s <em>being built.</em>'}</h2><p>From weekend experiments to the next big thing. Find something worth exploring.</p></div><span class="subtle-counter" id="results-count"></span></div><div class="catalog-tools"><div class="search-box">${ic('search',19)}<input id="search-input" type="search" aria-label="Search projects" placeholder="Search projects, tech, ideas..." value="${esc(state.filter.q)}" autocomplete="off"/><span class="search-shortcut" aria-hidden="true">⌘ K</span></div><div class="select-shell">${ic('filter',17)}<select id="stage-select" aria-label="Filter by stage"><option value="All">All stages</option>${state.meta.stages.map(s=>`<option ${state.filter.stage===s?'selected':''}>${esc(s)}</option>`).join('')}</select>${ic('down',16)}</div><div class="select-shell">${ic('layers',17)}<select id="sort-select" aria-label="Sort projects"><option value="trending" ${state.filter.sort==='trending'?'selected':''}>Trending</option><option value="newest" ${state.filter.sort==='newest'?'selected':''}>Newest</option><option value="popular" ${state.filter.sort==='popular'?'selected':''}>Most viewed</option></select>${ic('down',16)}</div></div><div class="filter-pills" id="filter-pills">${filterPills()}</div><div class="catalog-results" id="catalog-results"><div class="catalog-loading">${ic('spark',24)} Discovering wonderful things...</div></div></div></section>`;}
function filterPills() {return ['All',...state.meta.categories].map(c=>`<button data-action="set-filter" data-category="${esc(c)}" class="pill ${state.filter.category===c?'pill-active':''}">${c==='All'?ic('grid',15):''}${esc(c)}</button>`).join('');}
function home() {frame(hero()+`<div class="ticker" aria-hidden="true"><div class="container ticker-in"><span>DESIGNED TO INSPIRE</span><i>✦</i><span>MADE TO BE SHARED</span><i>✦</i><span>BUILT BY REAL PEOPLE</span><i>✦</i><span>EVERY IDEA MATTERS</span><i>✦</i></div></div>`+categories()+catalog()+cta());loadProjects();}
function explore() {frame(`<section class="explore-intro"><div class="container"><div class="eyebrow"><span class="online-pulse"></span> YOUR NEXT FAVORITE PROJECT AWAITS</div><h1>Find your next <span class="gradient-word">obsession.</span></h1><p>Step into the creative universe of people who love making things.</p></div></section>`+catalog()+cta());loadProjects();}
function cta(){return `<section class="cta-section"><div class="container"><div class="cta-glass"><div class="cta-decoration">✳</div><div class="section-kicker">YOUR NEXT IDEA STARTS HERE</div><h2>Made something cool?<br/><em>Let the world see it.</em></h2><p>Big ideas, tiny experiments, half-finished side quests — there's room for all of it.</p><a href="/submit" data-link class="btn btn-light btn-large">Showcase your work ${ic('arrow',18)}</a></div></div></section>`;}

/* --------------------------------------------------------------- project list */
let requestToken=0;
async function loadProjects() {
 const panel=$('#catalog-results');if(!panel)return;
 const token=++requestToken;
 state.loading=true;
 const qs=new URLSearchParams({q:state.filter.q,category:state.filter.category,stage:state.filter.stage,sort:state.filter.sort,limit:'24'});
 try {const result=await api('/api/projects?'+qs);
   if(token!==requestToken||!$('#catalog-results'))return;
   state.projects=result.projects;state.pagination=result.pagination;
   $('#results-count').textContent=`${result.pagination.total} ${result.pagination.total===1?'creation':'creations'}`;
   $('#catalog-results').innerHTML=result.projects.length?`<div class="project-grid">${result.projects.map(projectCard).join('')}</div>${result.pagination.pages>1?`<div class="pagination-more"><button class="btn btn-ghost" data-action="load-more">Show more creations ${ic('arrow',17)}<small>${result.projects.length} of ${result.pagination.total} shown</small></button></div>`:''}`:emptyState();
   observeReveals();
 } catch(e) {if(token!==requestToken||!$('#catalog-results'))return;
   $('#catalog-results').innerHTML=`<div class="empty-state"><div class="empty-icon">${ic('alert',34)}</div><h3>Couldn't load projects.</h3><p>${esc(e.message)}</p><button class="btn btn-ghost" data-action="retry-results">Try again</button></div>`;}
 finally{if(token===requestToken)state.loading=false;}
}
function emptyState() {return `<div class="empty-state"><div class="empty-icon">${ic('search',34)}</div><h3>Nothing in this corner yet.</h3><p>Try a different search or category — or be the first to share something here.</p><button class="btn btn-ghost" data-action="clear-filters">Clear filters ${ic('arrow',16)}</button></div>`;}
async function loadMore(){
 if(state.loading || !state.pagination || state.pagination.page>=state.pagination.pages)return;
 const page=state.pagination.page+1;
 const qs=new URLSearchParams({q:state.filter.q,category:state.filter.category,stage:state.filter.stage,
      sort:state.filter.sort,limit:'24',page:String(page)});
 state.loading=true;const b=$('[data-action="load-more"]');if(b){b.disabled=true;b.classList.add('is-loading');}
 try{
  const result=await api('/api/projects?'+qs);
  const grid=$('#catalog-results .project-grid');if(!grid)return;
  grid.insertAdjacentHTML('beforeend',result.projects.map(projectCard).join(''));
  state.projects.push(...result.projects);state.pagination=result.pagination;
  const more=$('.pagination-more');
  if(result.pagination.page>=result.pagination.pages){const shown=more?$('.pagination-more small'):null;if(shown)shown.textContent=`${state.projects.length} of ${result.pagination.total} shown — that's everything`;if(more)more.querySelector('button').remove();}
  else if(b){b.disabled=false;b.classList.remove('is-loading');b.innerHTML='Show more creations '+ic('arrow',17)+`<small>${state.projects.length} of ${result.pagination.total} shown</small>`;}
  observeReveals();
 }catch(e){if(b){b.disabled=false;b.classList.remove('is-loading');}toast('Could not load more projects right now.','error');}
 finally{state.loading=false;}
}

/* ------------------------------------------------------------- scroll reveals */
let revealObserver;
function observeReveals() {
  const nodes=$$('.reveal:not(.is-visible)');
  if(reduces()||!('IntersectionObserver' in window)){nodes.forEach(el=>el.classList.add('is-visible'));return;}
  if(!revealObserver)revealObserver=new IntersectionObserver(entries=>{
    entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('is-visible');revealObserver.unobserve(entry.target);}});
  },{rootMargin:'0px 0px -6% 0px',threshold:0.05});
  nodes.forEach(el=>revealObserver.observe(el));
}

/* ------------------------------------------------------------------------ auth */
function panelLogin(type='login') {const sign=type==='join';const meter=sign?`<span class="pw-meter" id="pw-meter" aria-live="polite"><b></b><em>Add letters and numbers</em></span>`:'';return `<section class="auth-page"><div class="container auth-layout"><div class="auth-story"><div class="auth-orb"></div><div class="eyebrow"><span class="online-pulse"></span> THE CREATOR COMMUNITY</div><h1>Every maker has <span class="gradient-word">a story.</span></h1><p>Yours deserves a place to shine. Join a space made for the beautifully curious.</p><div class="auth-points"><span>${ic('circle',17)} Showcase anything you've built</span><span>${ic('circle',17)} Discover interesting makers</span><span>${ic('circle',17)} Give your ideas a home</span></div></div><div class="auth-card glass-panel"><div class="auth-card-head"><span class="auth-emblem">${ic('spark',22)}</span><h2>${sign?'Create your account':'Welcome back'}</h2><p>${sign?'Join the community. Your next chapter starts here.':'The maker universe missed you.'}</p></div><form id="auth-form" data-form="${sign?'register':'login'}">${sign?`<div class="input-group"><label for="full-name">Your name</label><input required id="full-name" name="name" minlength="2" maxlength="70" placeholder="e.g. Alex Rivera"/></div><div class="input-group"><label for="user-name">Username</label><input required id="user-name" name="username" minlength="3" maxlength="25" pattern="[a-zA-Z0-9_]+" placeholder="alexbuilds" autocomplete="username"/></div>`:''}<div class="input-group"><label for="email">${sign?'Email address':'Email or username'}</label><input required id="email" name="email" ${sign?'type="email"':'type="text"'} placeholder="${sign?'you@example.com':'Email or username'}" autocomplete="${sign?'email':'username'}"/></div><div class="input-group"><label for="password">Password</label><input required id="password" name="password" type="password" minlength="8" placeholder="At least 8 characters" autocomplete="${sign?'new-password':'current-password'}"/>${meter}</div><div class="form-error" id="auth-error" role="alert"></div><button type="submit" class="btn btn-primary form-submit">${sign?'Create free account':'Log in'} ${ic('arrow',18)}</button></form><div class="auth-switch">${sign?'Already part of Univorya?':'New around here?'} <a href="/${sign?'login':'join'}" data-link>${sign?'Log in':'Create an account'}</a></div><p class="auth-note">Your account is stored in this MVP's local database. No external sign-in required.</p></div></div></section>`;}
function authPage(type){frame(panelLogin(type),'auth-main');}

/* ------------------------------------------------------------------------ form */
const DRAFT_KEY='univorya.draft';
function formGroup(label,id,field,placeholder,required=true,limits=null){
 const rules=[required?'required':'',limits?`minlength="${limits[0]}"`:'' ,limits?`maxlength="${limits[1]}"`:''].filter(Boolean).join(' ');
 return `<div class="input-group"><label for="${id}">${label} ${required?'<span class="required">*</span>':''}</label><input id="${id}" name="${field}" ${rules} ${limits?`data-count="${limits[0]}-${limits[1]}"`:''} placeholder="${placeholder}" /></div>`;
}
function mediaTile(item,index){
 const dropped=item.keep===false;
 return `<li class="media-tile${dropped?' is-dropped':''}" data-media-id="${item.id}"><img src="${esc(item.file)}" alt="${esc(item.kind==='cover'?'Project cover':'Screenshot')} ${index+1}" loading="lazy" data-full="${esc(item.file)}" data-caption="${esc(item.kind==='cover'?'Cover image':'Screenshot')} ${index+1} · ${item.width}×${item.height}"/><div class="media-tile-meta"><span>${esc(item.kind==='cover'?'Cover':'Shot')}</span>${item.uploaded_at?`<time datetime="${esc(item.uploaded_at)}">${formatDate(item.uploaded_at)}</time>`:''}<b>${item.width}×${item.height}</b>${item.animated?'<i class="shot-flag">animated</i>':''}</div>${item.kind==='screenshot'?`<button type="button" class="media-remove" data-action="${dropped?'restore-media':'drop-existing-media'}" data-media-id="${item.id}" aria-label="${dropped?'Keep':'Remove'} this screenshot">${ic(dropped?'plus':'close',14)}</button>`:''}</li>`;
}
function pickedTile(file,index){
 return `<li class="media-tile is-new" data-index="${index}"><img src="${file.url}" alt="Screenshot ${index+1} preview"/><div class="media-tile-meta"><span>New</span><b>${file.width?file.width+'×'+file.height:'reading…'}</b>${file.size?'<i>'+kb(file.size)+'</i>':''}</div><button type="button" class="media-remove" data-action="drop-picked-media" data-index="${index}" aria-label="Remove ${esc(file.name)}">${ic('close',14)}</button></li>`;
}
function renderMediaList(){
 const list=$('#media-list');if(!list)return;
 const existing=state.existingMedia.map((item,i)=>mediaTile(item,i));
 const picked=state.pickedScreens.map((file,i)=>pickedTile(file,i));
 list.innerHTML=existing.concat(picked).join('');
 const zone=$('#media-zone');if(zone)zone.classList.toggle('has-media',existing.length+picked.length>0);
 const kept=state.existingMedia.filter(item=>item.keep!==false).length+picked.length;
 const counter=$('#media-count');
 if(counter)counter.textContent=`${kept} of ${state.meta.limits.max_media+1} images used${state.existingMedia.some(i=>i.keep===false)?' · some removed on Save':''}`;
}
function submitPage(){if(!state.user){state.pendingAfterAuth='/submit';frame(`<section class="gate-page"><div class="container"><div class="gate-card glass-panel"><span class="auth-emblem">${ic('rocket',26)}</span><h1>You're one step away from sharing.</h1><p>Make a free creator account first, then give your project the spotlight it deserves.</p><div class="hero-buttons"><a href="/join" data-link class="btn btn-primary btn-large">Join for free ${ic('arrow',18)}</a><a href="/login" data-link class="btn btn-ghost btn-large">I have an account</a></div></div></div></section>`);return;}
const L=state.meta.limits;
if(state.keepDraft){state.keepDraft=false;}else{releasePicks();state.existingMedia=[];}
frame(`<section class="submit-section"><div class="container submit-layout"><div class="submit-intro"><div class="eyebrow"><span class="online-pulse"></span> BRING IT INTO THE WORLD</div><h1>Show the world <span class="gradient-word">what you've made.</span></h1><p>From tiny experiments to polished products, every creation has a story worth sharing.</p><div class="submission-tips"><div>${ic('spark',19)} <span>Any kind of project or idea is welcome.</span></div><div>${ic('eye',19)} <span>Add a great cover image to stand out.</span></div><div>${ic('heart',19)} <span>Share something genuine. Credit others.</span></div></div></div><div class="submit-card glass-panel"><div class="form-header"><div><span class="step-label">YOUR CREATION · STEP 01 / 01</span><h2>Project details</h2><p>Tell the community what you're building.</p></div><span class="form-planet">✳</span></div><form id="project-form" data-form="project" enctype="multipart/form-data" novalidate><div class="cover-upload" id="cover-zone"><input aria-label="Upload project cover image" type="file" id="cover" name="cover" accept="image/png,image/jpeg,image/webp,image/gif"/><div id="cover-preview"><span class="upload-ico">${ic('upload',24)}</span><strong>Click to upload a cover image</strong><small>PNG, JPG, WebP or GIF · max ${L.max_file_mb} MB · at least ${L.min_width}×${L.min_height}px</small></div></div>${formGroup('Project name','title','title','Give it a memorable name',true,[3,80])}${formGroup('One-line tagline','tagline','tagline','What does your project do in one sentence?',true,[10,140])}<div class="input-group"><label for="description">The story behind it <span class="required">*</span></label><textarea id="description" name="description" required minlength="30" maxlength="5000" rows="5" placeholder="What inspired you? What problem does it solve? What makes it special?"></textarea><span class="field-hint" id="description-hint">Minimum 30 characters</span></div><div class="form-grid"><div class="input-group"><label for="category">Category <span class="required">*</span></label><select name="category" id="category" required>${state.meta.categories.map(c=>`<option>${esc(c)}</option>`).join('')}</select></div><div class="input-group"><label for="stage">Current stage <span class="required">*</span></label><select name="stage" id="stage" required>${state.meta.stages.map(s=>`<option ${s==='Building'?'selected':''}>${esc(s)}</option>`).join('')}</select></div></div>${formGroup('Tech stack','tech','tech','React, Python, Next.js (comma-separated)',false,[0,280])}${formGroup('Tags','tags','tags','Productivity, Open source, Education',false,[0,280])}<div class="form-divider"><span>SCREENSHOTS</span></div><div class="media-zone" id="media-zone"><label class="media-pick" for="screenshots">${ic('image',20)} <span>Add screenshots</span><small>Up to ${L.max_media} images · ${L.max_file_mb} MB each</small></label><input type="file" id="screenshots" accept="image/png,image/jpeg,image/webp,image/gif" multiple/><ul class="media-list" id="media-list" aria-live="polite"></ul><span class="media-count" id="media-count"></span></div><div class="form-divider"><span>LINKS & DETAILS</span></div>${formGroup('Source repository','repo_url','repo_url','https://github.com/yourname/project',false,[0,500])}${formGroup('Live demo','demo_url','demo_url','https://your-project.com',false,[0,500])}${formGroup('Project website','website_url','website_url','https://example.com',false,[0,500])}<div class="form-grid"><div class="input-group"><label for="license">License (if applicable)</label><input id="license" name="license" placeholder="e.g. MIT, Apache-2.0" maxlength="70" /></div><div class="input-group"><label for="cover_style">Fallback cover style</label><select name="cover_style" id="cover_style">${state.meta.cover_styles.map(s=>`<option value="${esc(s)}">${esc(({violet:'Cosmic violet',blue:'Electric blue',mint:'Fresh mint',orange:'Golden orange',pink:'Dreamy pink',slate:'Deep slate'})[s]||s)}</option>`).join('')}</select></div></div><div class="form-error" id="project-error" role="alert"></div><div class="form-bottom"><span id="draft-note">${ic('circle',16)} You can remove your project later.</span><button type="submit" class="btn btn-primary btn-large">Publish my project ${ic('arrow',18)}</button></div></form></div></div></section>`,'form-main');
renderMediaList();renderCoverPreview();restoreDraft();
}
function saveDraft(){if(location.pathname!=='/submit'||!$('#project-form'))return;
 const data={};$$('#project-form').length&&$$('#project-form [name]').forEach(el=>{if(el.type==='file')return;data[el.name]=el.value;});
 try{sessionStorage.setItem(DRAFT_KEY,JSON.stringify(data));}catch{}}
function restoreDraft(){
 if(location.pathname!=='/submit')return;
 let data=null;try{data=JSON.parse(sessionStorage.getItem(DRAFT_KEY)||'null');}catch{}
 if(!data)return;
 for(const [name,value] of Object.entries(data)){const el=$('#project-form [name="'+name+'"]');if(el&&el.type!=='file')el.value=value;}
 const note=$('#draft-note');if(note)note.innerHTML=`${ic('spark',16)} We restored your unfinished draft after signing you back in.`;
 toast('Draft restored — nothing you typed or picked was lost.');
}
function clearDraft(){try{sessionStorage.removeItem(DRAFT_KEY);}catch{}}

function projectPage(slug){frame(`<section class="page-loading"><div class="container"><div class="catalog-loading">${ic('spark',26)} Loading project...</div></div></section>`);api('/api/projects/'+encodeURIComponent(slug)).then(({project:p})=>{
 if(state.route!=='/p/'+slug)return;
 const links=[['Live demo','demo_url','external'],['Source code','repo_url','github'],['Website','website_url','globe']].filter(x=>p[x[1]]);
 const shots=(p.media||[]).map((m,i)=>({m,i})).filter(x=>x.m.kind==='screenshot');
 state.lightbox=-1;state.currentProject=p;
 frame(`<section class="detail-section"><div class="container"><nav class="breadcrumb" aria-label="Breadcrumb"><a href="/explore" data-link>Explore</a>${ic('chevron',15)}<span>${esc(p.category)}</span>${ic('chevron',15)}<span class="crumb-current">${esc(p.title)}</span></nav><div class="detail-hero"><div class="detail-header"><div class="detail-eyebrow"><span class="stage stage-${p.stage.toLowerCase()}"><i></i>${esc(p.stage)}</span><span class="detail-category">${ic(categoryIcon(p.category),16)} ${esc(p.category)}</span>${p.is_demo?'<span class="demo-detail">FICTIONAL SAMPLE</span>':''}</div><h1 class="clamp-title">${esc(p.title)}<span class="title-dot">.</span></h1><p class="clamp-tagline">${esc(p.tagline)}</p><div class="detail-meta"><a href="/makers/${encodeURIComponent(p.creator.username)}" data-link class="detail-author">${usernameAvatar(p.creator)} <span><strong>${esc(p.creator.name)}</strong><small>@${esc(p.creator.username)}</small></span></a><div class="meta-stat">${ic('calendar',16)} ${formatDate(p.created_at)}</div><div class="meta-stat">${ic('eye',16)} <span class="count-up" data-to="${p.views}">0</span> views</div></div><div class="detail-actions">${links.map(([text,k,i])=>`<a class="btn ${k==='demo_url'?'btn-primary':'btn-ghost'}" href="${esc(p[k])}" target="_blank" rel="noopener noreferrer">${ic(i,17)} ${text} ${ic('external',14)}</a>`).join('')}<button class="btn btn-outline ${p.liked?'liked':''}" data-action="like" data-slug="${esc(p.slug)}">${ic('heart',18)} ${p.liked?'Liked':'Appreciate'} <span id="like-count">${p.likes}</span></button></div></div><div class="detail-art reveal">${art(p,true)}</div></div><div class="detail-content"><div class="detail-article reveal"><div class="section-kicker">THE BIG IDEA</div><h2>About this project</h2><div class="project-description">${esc(p.description).replace(/\n/g,'<br>')}</div>${p.is_demo?'<div class="sample-callout">✦ This is fictional sample content used to demonstrate the gallery. It is not a real creator submission.</div>':''}${shots.length?`<div class="section-kicker little-top">FROM THE PROJECT</div><h2>Screenshots <small>${shots.length}</small></h2><div class="shot-grid">${shots.map((s,n)=>`<button class="shot reveal" style="--i:${n}" data-action="open-lightbox" data-index="${s.i}" aria-label="Enlarge screenshot ${n+1}"><img src="${esc(s.m.file)}" alt="${esc(p.title)} screenshot ${n+1}" loading="lazy" width="${s.m.width}" height="${s.m.height}"/><span class="shot-bar"><b>${s.m.width}×${s.m.height}</b><i>${kb(s.m.bytes)}</i>${s.m.animated?'<em class="shot-flag">animated</em>':''}</span></button>`).join('')}</div>`:''}<div class="section-kicker little-top">BUILT WITH</div><h3>Under the hood</h3><div class="detail-tech">${p.tech.length?p.tech.map(x=>`<span>${ic('code',15)} ${esc(x)}</span>`).join(''):'<span>Tech stack not specified</span>'}</div>${p.tags.length?`<h3 class="tags-title">Tags</h3><div class="detail-tags">${p.tags.map(x=>`<span>#${esc(x)}</span>`).join('')}</div>`:''}</div><aside class="detail-aside"><div class="aside-box glass-panel reveal"><div class="aside-heading">THE MAKER</div><div class="aside-author">${usernameAvatar(p.creator)}<div><strong>${esc(p.creator.name)}</strong><small>@${esc(p.creator.username)}</small></div></div><a class="btn btn-outline full-width" href="/makers/${encodeURIComponent(p.creator.username)}" data-link>View creator's projects ${ic('arrow',16)}</a></div><div class="aside-box glass-panel reveal"><div class="aside-heading">PROJECT SNAPSHOT</div><div class="snapshot-row"><span>Stage</span><strong>${esc(p.stage)}</strong></div><div class="snapshot-row"><span>Category</span><strong>${esc(p.category)}</strong></div><div class="snapshot-row"><span>License</span><strong>${esc(p.license||'Not specified')}</strong></div><div class="snapshot-row"><span>Appreciations</span><strong>${p.likes}</strong></div><div class="snapshot-row"><span>Views</span><strong>${p.views}</strong></div>${(p.media||[]).length?`<div class="snapshot-row"><span>Uploads</span><strong>${p.media.length} image${p.media.length>1?'s':''}</strong></div>`:''}</div>${p.is_owner?`<a href="/edit/${encodeURIComponent(p.slug)}" data-link class="btn btn-outline full-width edit-project-btn">${ic('code',16)} Edit project</a><button data-action="delete" data-slug="${esc(p.slug)}" class="danger-button">${ic('trash',16)} Delete project</button>`:''}</aside></div></div></section>`);
 document.title=`${p.title} · Univorya`;
 countUp();observeReveals();
 }).catch(e=>frame(`<section class="gate-page"><div class="container"><div class="gate-card"><h1>Project not found.</h1><p>${esc(e.message)}</p><a href="/explore" data-link class="btn btn-primary">Explore projects ${ic('arrow',18)}</a></div></div></section>`));}
function countUp(){if(reduces()){$$('.count-up').forEach(el=>el.textContent=el.dataset.to);return;}
 $$('.count-up').forEach(el=>{const to=Number(el.dataset.to)||0,start=performance.now(),dur=900;
  const step=now=>{const t=Math.min((now-start)/dur,1);el.textContent=Math.round(to*(1-Math.pow(1-t,3)));if(t<1)requestAnimationFrame(step);};requestAnimationFrame(step);});}

async function editPage(slug){
 if(!state.user){state.pendingAfterAuth='/edit/'+encodeURIComponent(slug);navigate('/login');return;}
 try{
  const {project:p}=await api('/api/projects/'+encodeURIComponent(slug));
  if(state.route!=='/edit/'+slug)return;
  if(!p.is_owner){frame('<section class="gate-page"><div class="container"><div class="gate-card glass-panel"><h1>Permission denied</h1><p>Only the creator can edit this project.</p><a data-link href="/dashboard" class="btn btn-primary">My dashboard</a></div></div></section>');return;}
  submitPage();
  if(location.pathname!=='/edit/'+slug)return;
  const form=$('#project-form');if(!form)return;
  form.dataset.editSlug=slug;
  const vals={title:p.title,tagline:p.tagline,description:p.description,category:p.category,stage:p.stage,
    tech:p.tech.join(', '),tags:p.tags.join(', '),repo_url:p.repo_url,demo_url:p.demo_url,
    website_url:p.website_url,license:p.license,cover_style:p.cover_style};
  for(const [k,v] of Object.entries(vals)){const el=form.elements.namedItem(k);if(el)el.value=v;}
  state.existingMedia=p.media.map(m=>({...m,keep:true}));
  renderMediaList();renderCoverPreview();clearDraft();
  const button=form.querySelector('[type="submit"]');button.innerHTML='Save changes '+ic('arrow',18);
  $('.form-header h2').textContent='Edit project';
  $('.form-header p').textContent='Update your creation. Keep the same public link.';
  const heading=$('.submit-intro h1');heading.innerHTML='Keep your <span class="gradient-word">story evolving.</span>';
 }catch(e){frame('<section class="gate-page"><div class="container"><div class="gate-card"><h1>Cannot edit project</h1><p>'+esc(e.message)+'</p><a href="/dashboard" data-link class="btn btn-primary">My dashboard</a></div></div></section>');}
}
function dashboard(){if(!state.user){navigate('/login');return;}frame(`<section class="dashboard-page"><div class="container"><div class="dashboard-top"><div><div class="eyebrow"><span class="online-pulse"></span> YOUR CREATOR SPACE</div><h1>Good to see you, <span class="gradient-word">${esc(state.user.name.split(' ')[0])}.</span></h1><p>Everything you've shared with the world, all in one place.</p></div><div class="dashboard-actions"><button class="btn btn-ghost" data-action="logout">${ic('logout',16)} Log out</button><a href="/submit" data-link class="btn btn-primary">${ic('plus',17)} New project</a></div></div><div id="dashboard-projects"><div class="catalog-loading">Loading your projects...</div></div></div></section>`);
 api('/api/projects?creator='+encodeURIComponent(state.user.username)+'&sort=newest').then(d=>{const el=$('#dashboard-projects');if(!el)return;el.innerHTML=d.projects.length?`<div class="dashboard-count">${d.projects.length} published ${d.projects.length===1?'creation':'creations'}</div><div class="project-grid">${d.projects.map(projectCard).join('')}</div>`:`<div class="dashboard-empty glass-panel">${ic('rocket',34)}<h2>Your portfolio is a blank canvas.</h2><p>Every great maker starts with their first project. Ready to share yours?</p><a class="btn btn-primary" href="/submit" data-link>Publish your first project ${ic('arrow',17)}</a></div>`;observeReveals();}).catch(e=>toast('Could not load your projects right now.','error'));}
function makerPage(username){
 frame(`<section class="dashboard-page"><div class="container"><div class="page-loading-inline"><div class="catalog-loading">${ic('spark',22)} Loading creator...</div></div></div></section>`);
 api('/api/makers/'+encodeURIComponent(username)).then(({maker})=>{
  if(state.route!=='/makers/'+username)return;
  frame(`<section class="dashboard-page"><div class="container"><div class="maker-header reveal"><div class="maker-id">${usernameAvatar(maker)}<div><div class="eyebrow">✦ COMMUNITY CREATOR</div><h1 class="clamp-title"><span class="gradient-word">@${esc(maker.username)}</span></h1><p>${esc(maker.name)} · joined ${formatDate(maker.joined_at)}</p></div></div><div class="maker-stats"><div class="stat"><b class="count-up" data-to="${maker.project_count}">0</b><small>projects</small></div><div class="stat"><b class="count-up" data-to="${maker.views}">0</b><small>views</small></div><div class="stat"><b class="count-up" data-to="${maker.likes}">0</b><small>appreciations</small></div></div><a href="/explore" data-link class="btn btn-ghost">${ic('arrow',17)} Explore more</a></div><div id="maker-projects" class="catalog-loading">Loading creator projects...</div></div></section>`);
  countUp();observeReveals();
  api('/api/projects?creator='+encodeURIComponent(maker.username)).then(d=>{const el=$('#maker-projects');if(!el)return;el.innerHTML=d.projects.length?`<div class="dashboard-count">${d.projects.length} creations</div><div class="project-grid">${d.projects.map(projectCard).join('')}</div>`:`<div class="empty-state">No public projects yet for this creator.</div>`;observeReveals();}).catch(()=>toast('Could not load this creator\'s projects.','error'));
 }).catch(e=>frame(`<section class="gate-page"><div class="container"><div class="gate-card"><h1>No creator found.</h1><p>${esc(e.message)}</p><a href="/explore" data-link class="btn btn-primary">Explore projects ${ic('arrow',18)}</a></div></div></section>`));}

/* -------------------------------------------------------------------- drawer */
function openDrawer(){
 const nav=$('.nav-links');if(!nav||nav.classList.contains('mobile-open'))return;
 nav.classList.add('mobile-open');
 // The header paints a backdrop-filter, which makes it the containing block for any
 // fixed child — so the veil lives on <body> where inset:0 really means the viewport.
 const veil=document.createElement('div');veil.className='drawer-veil';veil.dataset.action='close-menu';
 document.body.append(veil);
 const button=$('[data-action="menu"]');if(button)button.setAttribute('aria-expanded','true');
 document.body.classList.add('drawer-open');
 drawerTrap.nav=nav;drawerTrap.previous=document.activeElement;
 document.addEventListener('keydown',drawerKeys,true);
 nav.querySelector('a,button')?.focus();
}
function closeDrawer(){
 const nav=$('.nav-links');
 const veil=$('.drawer-veil');
 if(veil)veil.remove();
 document.body.classList.remove('drawer-open');
 document.removeEventListener('keydown',drawerKeys,true);
 if(!nav||!nav.classList.contains('mobile-open'))return;
 nav.classList.remove('mobile-open');
 const button=$('[data-action="menu"]');if(button)button.setAttribute('aria-expanded','false');
 if(drawerTrap.previous&&document.contains(drawerTrap.previous))drawerTrap.previous.focus();
}
const drawerTrap={nav:null,previous:null};
function drawerKeys(e){
 const nav=drawerTrap.nav;if(!nav||!nav.classList.contains('mobile-open'))return;
 if(e.key==='Escape'){e.preventDefault();closeDrawer();$('[data-action="menu"]')?.focus();return;}
 if(e.key!=='Tab')return;
 const stops=$$('a[href],button:not([disabled])',nav).filter(el=>el.offsetParent!==null||el.classList.contains('nav-signout'));
 if(!stops.length)return;
 const first=stops[0],last=stops[stops.length-1];
 if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
 else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
}

/* ------------------------------------------------------------------ lightbox */
function openLightbox(index){
 const p=state.currentProject;if(!p)return;
 const all=(p.media||[]);index=Math.max(0,Math.min(index,all.length-1));
 state.lightbox=index;const item=all[index];
 const screenshots=all.filter(m=>m.kind==='screenshot');
 const label=item.kind==='cover'?'Cover image':`Screenshot ${screenshots.indexOf(item)+1} of ${screenshots.length}`;
 let box=$('#lightbox');
 if(!box){box=document.createElement('div');box.id='lightbox';box.className='lightbox';box.setAttribute('role','dialog');box.setAttribute('aria-modal','true');box.setAttribute('aria-label','Project image');document.body.append(box);}
 box.innerHTML=`<button class="lightbox-close icon-button" data-action="close-lightbox" aria-label="Close image">${ic('close',22)}</button><figure><img src="${esc(item.file)}" alt="Enlarged project image"/><figcaption>${esc(label)} · ${item.width}×${item.height} · ${kb(item.bytes)}${item.animated?' · animated':''}</figcaption></figure>${all.length>1?`<button class="lightbox-nav prev" data-action="lightbox-prev" aria-label="Previous image">${ic('chevron',22)}</button><button class="lightbox-nav next" data-action="lightbox-next" aria-label="Next image">${ic('chevron',22)}</button>`:''}`;
 box.classList.add('is-open');document.body.classList.add('lightbox-open');
 state.lightboxTrigger=document.activeElement;
 box.querySelector('.lightbox-close').focus();
 document.addEventListener('keydown',lightboxKeys,true);
}
function closeLightbox(){
 const box=$('#lightbox');if(!box)return;
 box.classList.remove('is-open');document.body.classList.remove('lightbox-open');
 document.removeEventListener('keydown',lightboxKeys,true);
 state.lightbox=-1;
 if(state.lightboxTrigger&&document.contains(state.lightboxTrigger))state.lightboxTrigger.focus();
}
function lightboxKeys(e){
 if(e.key==='Escape'){e.preventDefault();closeLightbox();return;}
 const total=(state.currentProject?.media||[]).length;
 if(e.key==='ArrowRight'&&total>1){e.preventDefault();openLightbox(state.lightbox+1);}
 if(e.key==='ArrowLeft'&&total>1){e.preventDefault();openLightbox((state.lightbox-1+total)%total);}
}

/* --------------------------------------------------------------------- media */
async function imageInfo(file){
 const url=URL.createObjectURL(file);
 return new Promise(resolve=>{
  const img=new Image();
  img.onload=()=>{resolve({file,url,width:img.naturalWidth,height:img.naturalHeight,size:file.size,name:file.name});};
  img.onerror=()=>{URL.revokeObjectURL(url);resolve(null);};
  img.src=url;
 });
}
async function addScreenshots(files){
 const L=state.meta.limits, kept=state.existingMedia.filter(m=>m.keep!==false).length;
 const room=L.max_media-kept;
 if(room<=0){toast(`The cover plus ${L.max_media} screenshots is the limit.`,'error');return;}
 const list=[...files].slice(0,room);
 if(files.length>list.length)toast(`Only ${room} more screenshot${room===1?'':'s'} fit on this project.`,'error');
 for(const file of list){
  if(file.size>L.max_file_mb*1024*1024){toast(`${file.name} is over ${L.max_file_mb} MB.`,'error');continue;}
  if(!/image\/(png|jpe?g|webp|gif)/.test(file.type)){toast(`${file.name} isn't a PNG, JPG, WebP or GIF.`,'error');continue;}
  const info=await imageInfo(file);
  if(!info){toast(`${file.name} could not be read — it may be corrupted.`,'error');continue;}
  if(info.width<L.min_width||info.height<L.min_height){
   URL.revokeObjectURL(info.url);
   toast(`${file.name} is ${info.width}×${info.height}. Use at least ${L.min_width}×${L.min_height}.`,'error');continue;}
  state.pickedScreens.push(info);
 }
 renderMediaList();
 const input=$('#screenshots');if(input)input.value='';
}

/* ---------------------------------------------------------------------- init */
function notFoundPage(path){
 const shown=dec(path).slice(0,80);
 frame(`<section class="gate-page"><div class="container"><div class="gate-card glass-panel"><span class="auth-emblem">${ic('eye',26)}</span><h1>Nothing lives at this address.</h1><p><strong>${esc(shown)}</strong> isn't a Univorya page. It may have been renamed, deleted, or mistyped.</p><div class="hero-buttons"><a href="/explore" data-link class="btn btn-primary btn-large">Explore projects ${ic('arrow',18)}</a><a href="/" data-link class="btn btn-ghost btn-large">Back home</a></div></div></div></section>`);
 document.title='Page not found · Univorya';
}

function render(){
 state.route=location.pathname.replace(/\/+$/,'')||'/';
 window.scrollTo(0,0);
 const path=state.route;
 if(path==='/'){readFiltersFromUrl();home();}
 else if(path==='/explore'){readFiltersFromUrl();explore();}
 else if(path==='/login')authPage('login');
 else if(path==='/join')authPage('join');
 else if(path==='/submit')submitPage();
 else if(path.startsWith('/edit/'))editPage(dec(path.substring(6)));
 else if(path==='/dashboard')dashboard();
 else if(path.startsWith('/p/'))projectPage(dec(path.substring(3)));
 else if(path.startsWith('/makers/'))makerPage(dec(path.substring(8)));
 else notFoundPage(path);
}
let debounce;
document.addEventListener('input',e=>{
 if(e.target.id==='search-input'){state.filter.q=e.target.value;clearTimeout(debounce);debounce=setTimeout(()=>{writeFiltersToUrl();loadProjects();},320);}
 if(e.target.id==='password'&&state.route!=='/submit'){const value=e.target.value,classes=(/[A-Za-z]/.test(value)?1:0)+(/\d/.test(value)?1:0)+(/[^A-Za-z0-9\s]/.test(value)?1:0);
  const meter=$('#pw-meter');if(meter){const score=value.length>=12&&classes>=3?3:classes>=2&&value.length>=8?2:value.length?1:0;
   meter.dataset.score=score;meter.querySelector('em').textContent=['Add letters and numbers','Okay — mix in a symbol','Strong','Very strong'][score];}}
 if(e.target.dataset.count&&$('#project-form')?.contains(e.target)){const [min,max]=e.target.dataset.count.split('-').map(Number);
  const hint=e.target.closest('.input-group').querySelector('.field-hint')||e.target.parentElement.querySelector('.field-hint');
  if(hint&&e.target.value.length<min)hint.textContent=`${min-e.target.value.length} more characters needed`;}
 if($('#project-form')?.contains(e.target))saveDraft();
});
document.addEventListener('change',async e=>{
 if(e.target.id==='sort-select'){state.filter.sort=e.target.value;writeFiltersToUrl(true);loadProjects();}
 if(e.target.id==='stage-select'){state.filter.stage=e.target.value;writeFiltersToUrl(true);loadProjects();}
 if(e.target.id==='cover'){const f=e.target.files?.[0];if(!f)return;
  const L=state.meta.limits;
  if(f.size>L.max_file_mb*1024*1024){toast(`Cover images are limited to ${L.max_file_mb} MB.`,'error');e.target.value='';return;}
  const info=await imageInfo(f);
  if(!info){toast('That cover could not be read — try another file.','error');e.target.value='';return;}
  if(info.width<L.min_width||info.height<L.min_height){URL.revokeObjectURL(info.url);e.target.value='';
   toast(`That cover is ${info.width}×${info.height}. Use at least ${L.min_width}×${L.min_height}.`,'error');return;}
  if(state.pickedCover)URL.revokeObjectURL(state.pickedCover.url);
  state.pickedCover=info;renderCoverPreview();}
 if(e.target.id==='screenshots'){await addScreenshots(e.target.files);}
});
document.addEventListener('click',async e=>{
 const a=e.target.closest('[data-link]');if(a){e.preventDefault();closeDrawer();navigate(new URL(a.href,location.origin).pathname+new URL(a.href,location.origin).search);return;}
 const b=e.target.closest('[data-action]');if(!b)return;const action=b.dataset.action;
 if(action==='menu'){document.querySelector('.nav-links').classList.contains('mobile-open')?closeDrawer():openDrawer();return;}
 // CSP allows script-src 'self' only, so an inline onclick attribute would simply do nothing.
 if(action==='retry-boot'){location.reload();return;}
 if(action==='close-menu'){closeDrawer();return;}
 if(action==='logout'){try{await api('/api/auth/logout',{method:'POST'});state.user=null;state.pendingAfterAuth='/dashboard';toast('You are signed out');navigate('/');}catch(err){toast(err.message,'error');}return;}
 if(action==='set-filter'||action==='pick-category'||action==='category-shortcut'){state.filter.category=b.dataset.category||'All';
  if(action==='category-shortcut'||action==='pick-category'){navigate('/explore?category='+encodeURIComponent(state.filter.category));}
  else{$('#filter-pills').innerHTML=filterPills();$$('.category-card').forEach(el=>el.classList.toggle('cat-active',el.dataset.category===state.filter.category));writeFiltersToUrl(true);loadProjects();}return;}
 if(action==='retry-results'){loadProjects();return;}
 if(action==='load-more'){await loadMore();return;}
 if(action==='clear-filters'){state.filter={q:'',category:'All',stage:'All',sort:'trending'};navigate('/explore');return;}
 if(action==='like'){if(!state.user){state.pendingAfterAuth=location.pathname;toast('Log in to appreciate projects','error');navigate('/login');return;}b.disabled=true;try{const d=await api('/api/projects/'+encodeURIComponent(b.dataset.slug)+'/like',{method:'POST'});b.innerHTML=`${ic('heart',18)} ${d.liked?'Liked':'Appreciate'} <span id="like-count">${d.likes}</span>`;b.classList.toggle('liked',d.liked);if(d.liked&&!reduces()){b.classList.remove('pulse');void b.offsetWidth;b.classList.add('pulse');}}catch(err){toast(err.message,'error');}finally{b.disabled=false;}return;}
 if(action==='delete'){if(!window.confirm('Permanently delete this project, its cover and its screenshots?'))return;try{await api('/api/projects/'+encodeURIComponent(b.dataset.slug),{method:'DELETE'});toast('Project deleted');navigate('/dashboard');}catch(err){toast(err.message,'error');}return;}
 if(action==='drop-picked-media'){const i=Number(b.dataset.index);const [removed]=state.pickedScreens.splice(i,1);if(removed)URL.revokeObjectURL(removed.url);renderMediaList();return;}
 if(action==='drop-existing-media'){const item=state.existingMedia.find(x=>String(x.id)===b.dataset.mediaId);if(item){item.keep=false;item.el&&item.el.remove();}renderMediaList();return;}
 if(action==='restore-media'){const item=state.existingMedia.find(x=>String(x.id)===b.dataset.mediaId);if(item){item.keep=true;renderMediaList();}return;}
 if(action==='open-lightbox'){openLightbox(Number(b.dataset.index||0));return;}
 if(action==='close-lightbox'){closeLightbox();return;}
 if(action==='lightbox-next'){const total=(state.currentProject?.media||[]).length;openLightbox((state.lightbox+1)%total);return;}
 if(action==='lightbox-prev'){const total=(state.currentProject?.media||[]).length;openLightbox((state.lightbox-1+total)%total);return;}
});
document.addEventListener('submit',async e=>{
 const form=e.target.closest('form[data-form]');if(!form)return;e.preventDefault();const type=form.dataset.form;const button=$('[type=submit]',form);const errorEl=$('.form-error',form);if(errorEl)errorEl.textContent='';button.disabled=true;button.classList.add('is-loading');
 try{
  if(type==='login'||type==='register'){
   const fd=new FormData(form);const body=Object.fromEntries(fd.entries());const result=await api('/api/auth/'+(type==='register'?'register':'login'),{method:'POST',body});state.user=result.user;toast(type==='register'?'Your creator account is ready!':'Welcome back!');const dest=state.pendingAfterAuth||'/dashboard';state.pendingAfterAuth='/dashboard';navigate(dest);
  }else if(type==='project'){
   const fd=new FormData(form);
   if(fd.get('cover')?.size){/* the browser still holds the freshly picked file */}
   else{fd.delete('cover');if(state.pickedCover)fd.set('cover',state.pickedCover.file,state.pickedCover.name);}
   state.pickedScreens.forEach(item=>fd.append('screenshots',item.file,item.name));
   const isEdit=Boolean(form.dataset.editSlug);
   if(isEdit)fd.append('keep_media',state.existingMedia.filter(m=>m.keep!==false&&m.kind==='screenshot').map(m=>m.id).join(','));
   const endpoint=isEdit?'/api/projects/'+encodeURIComponent(form.dataset.editSlug):'/api/projects';
   const result=await api(endpoint,{method:isEdit?'PUT':'POST',body:fd});
   clearDraft();releasePicks();
   toast(isEdit?'Project updated!':'Your project is now live on Univorya!');navigate(result.url);
  }
 }catch(err){
  if(err.status===401&&type==='project'){state.keepDraft=true;saveDraft();state.pendingAfterAuth=location.pathname;toast('Your session ended — sign in again to publish. Nothing you typed is lost.','error');navigate('/login');}
  else if(type==='project'&&err.status===0){toast('We couldn\'t reach the server. Your draft is kept — try again.','error');}
  else if(errorEl){errorEl.textContent=err.message;errorEl.scrollIntoView({behavior:reduces()?'auto':'smooth',block:'nearest'});}
  else toast(err.message,'error');
 }finally{button.disabled=false;button.classList.remove('is-loading');}
});
function releasePicks(){state.pickedScreens.forEach(item=>URL.revokeObjectURL(item.url));state.pickedScreens=[];
 if(state.pickedCover)URL.revokeObjectURL(state.pickedCover.url);state.pickedCover=null;}
function renderCoverPreview(){
 const box=$('#cover-preview');if(!box)return;
 const item=state.pickedCover||state.existingMedia.find(m=>m.kind==='cover');
 if(!item)return;
 const src=item.url||item.file;
 box.innerHTML=`<img class="upload-preview" src="${esc(src)}" alt="${item.url?'Your uploaded cover image preview':'Current project cover'}"/>`+
  `<span class="upload-change">${ic('upload',16)} ${item.url?'Change image':'Replace cover'}</span>`+
  `<span class="upload-spec">${item.width}×${item.height} · ${kb(item.size||item.bytes)}</span>`;
}

window.addEventListener('popstate',()=>{closeLightbox();closeDrawer();render();});
window.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();if(!$('#search-input'))navigate('/explore');setTimeout(()=>$('#search-input')?.focus(),30);}
 if($('#search-input')===document.activeElement&&e.key==='Escape'){e.target.value='';state.filter.q='';writeFiltersToUrl();loadProjects();}});
window.addEventListener('resize',()=>{if(window.innerWidth>900)closeDrawer();});
(async function init(){try{const [m,u]=await Promise.all([api('/api/metadata'),api('/api/auth/me')]);state.meta=m;state.user=u.user;}catch(e){$('#app').innerHTML=`<div class="init-error"><h1>Univorya</h1><h2>Couldn't connect to the backend.</h2><p>${esc(e.message)}</p><button class="btn btn-primary" data-action="retry-boot">Retry ${ic('arrow',16)}</button></div>`;return;}render();})();
