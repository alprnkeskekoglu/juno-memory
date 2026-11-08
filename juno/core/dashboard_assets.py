# ruff: noqa: E501
LIGHT = (
    "--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6f6d67;--grid:#e1e0d9;--axis:#c3c2b7;"
    "--ring:rgba(11,11,11,.10);--wash:rgba(11,11,11,.05);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;"
    "--other:#898781;--track:#cde2fb;--fill:#2a78d6;--good:#0ca30c;--warn:#fab219;--bad:#d03b3b"
)
DARK = (
    "--plane:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#a09e96;--grid:#2c2c2a;--axis:#383835;"
    "--ring:rgba(255,255,255,.10);--wash:rgba(255,255,255,.06);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;"
    "--other:#898781;--track:#1f3555;--fill:#3987e5;--good:#0ca30c;--warn:#fab219;--bad:#d03b3b"
)

CSS = (
    """
:root{color-scheme:light;"""
    + LIGHT
    + """}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){color-scheme:dark;"""
    + DARK
    + """}}
:root[data-theme=dark]{color-scheme:dark;"""
    + DARK
    + """}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--plane);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:1100px;margin:0 auto;padding:40px 24px 72px;display:grid;gap:28px}
h1{margin:0;font-size:26px;line-height:1.2;font-weight:600;letter-spacing:-.015em}
h2{margin:0;font-size:15px;font-weight:600}
h3{margin:28px 0 4px;font-size:14px;font-weight:600;color:var(--ink2)}
h3:first-of-type{margin-top:4px}
p{margin:0}
ul{margin:0;padding:0;list-style:none}
.muted{color:var(--muted)}
.na{color:var(--muted)}
.top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
.top .muted{margin-top:4px;font-size:13px}
.ic{width:16px;height:16px;flex:none}
.pill{display:inline-flex;align-items:center;gap:6px;padding:4px 12px 4px 9px;border:1px solid var(--ring);border-radius:999px;background:var(--surface);font-size:13px;font-weight:500;color:var(--ink);white-space:nowrap}
.pill.good .ic,.pill.st-good .ic{color:var(--good)}
.pill.warn .ic,.pill.st-warn .ic{color:var(--warn)}
.pill.bad .ic,.pill.st-bad .ic{color:var(--bad)}
.pill.neutral .ic{color:var(--ink2)}
.st-good,.st-warn,.st-bad{font-size:14px;padding:6px 16px 6px 12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}
.tile,.card{background:var(--surface);border:1px solid var(--ring);border-radius:14px}
.tile{padding:22px 24px;display:flex;flex-direction:column;gap:6px}
.tile h2{font-size:13px;font-weight:500;color:var(--ink2)}
.fig{font-size:36px;line-height:1.1;font-weight:600;letter-spacing:-.02em}
.hero .fig{font-size:56px;letter-spacing:-.03em}
.hero{grid-column:span 2}
.subs{display:grid;gap:2px;font-size:13px;color:var(--muted)}
.card{padding:24px 26px 20px}
.ch{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.ch .muted{font-size:13px}
.note{font-size:13px;margin:2px 0 6px}
.empty{padding:28px 0;text-align:center;color:var(--muted);font-size:14px}
.chart{margin:0}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.ax{fill:var(--muted);font-size:11px}
.val{fill:var(--ink);font-size:12px;font-weight:600}
.grid{stroke:var(--grid);stroke-width:1}
.base{stroke:var(--axis);stroke-width:1}
.hit{fill:transparent}
.col:focus{outline:none}
.col:hover .seg,.col:focus-visible .seg{opacity:.78}
.col:focus-visible .hit{stroke:var(--ink2);stroke-width:1}
.ln{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.dot{stroke:var(--surface);stroke-width:2}
.xh{stroke:var(--axis);stroke-width:1}
.lg{display:flex;flex-wrap:wrap;gap:6px 18px;margin:0 0 10px;font-size:13px;color:var(--ink2)}
.lg li{display:flex;align-items:center;gap:7px}
.sw{width:10px;height:10px;border-radius:3px;display:inline-block}
.ky{width:16px;height:2px;border-radius:1px;display:inline-block}
.tv{margin-top:10px;font-size:13px}
.tv summary{cursor:pointer;color:var(--muted);width:max-content}
.tv summary:hover{color:var(--ink)}
.tw{max-height:260px;overflow:auto;margin-top:8px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:5px 14px 5px 0;text-align:left;border-top:1px solid var(--grid);font-weight:400;white-space:nowrap}
thead th{position:sticky;top:0;background:var(--surface);color:var(--muted);font-size:12px;border-top:0}
tbody th{color:var(--ink2)}
.info{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;margin:0 0 0 6px;padding:0;border:1.5px solid var(--muted);border-radius:50%;background:transparent;color:var(--muted);font:italic 700 10px/1 Georgia,serif;cursor:help;vertical-align:middle}
.info:hover,.info:focus-visible{color:var(--ink);border-color:var(--ink)}
.info:focus-visible{outline:2px solid var(--s1);outline-offset:2px}
.gloss summary{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;cursor:pointer;list-style:none}
.gloss summary::-webkit-details-marker{display:none}
.gloss summary::before{content:"+";color:var(--muted);width:12px}
.gloss[open] summary::before{content:"\\2212"}
.gloss .muted{font-size:13px}
.gloss dl{margin:16px 0 0;display:grid;grid-template-columns:minmax(150px,220px) 1fr;gap:10px 24px}
.gloss dt{font-weight:600;font-size:14px}
.gloss dd{margin:0;color:var(--ink2);font-size:14px}
.tip{max-width:min(300px,calc(100vw - 16px));position:fixed;z-index:10;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--axis);border-radius:10px;padding:9px 12px;font-size:12px;box-shadow:0 6px 24px rgba(0,0,0,.16);min-width:140px}
.th{color:var(--ink2);margin-bottom:5px}
.tr{display:flex;align-items:center;gap:8px;line-height:1.7}
.tr i{order:-2;width:10px;height:3px;border-radius:2px;flex:none}
.tr b{margin-left:auto;padding-left:14px;font-variant-numeric:tabular-nums}
.tr span{order:-1;color:var(--ink2)}
.tr.plain span{margin-left:0}
.arms{display:grid;gap:0}
.arm{display:grid;gap:8px;padding:16px 0;border-top:1px solid var(--grid)}
.arm:first-child{border-top:0;padding-top:4px}
.arm-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.arm-m,.arm-f{display:flex;flex-wrap:wrap;gap:4px 18px;font-size:13px;color:var(--ink2)}
.arm-f{align-items:center;gap:4px 14px}
.arm-f .muted{font-size:13px}
.cmd{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--wash);padding:2px 7px;border-radius:6px;color:var(--ink2);user-select:all}
.bar{height:6px;border-radius:3px;background:var(--track);overflow:hidden;margin:2px 0}
.bar i{display:block;height:100%;background:var(--fill);border-radius:3px}
.qrow{font-size:13px;margin-top:14px;color:var(--ink2)}
.warnline{display:flex;gap:8px;align-items:flex-start;margin-top:10px;font-size:13px;color:var(--ink2)}
.warnline .ic{color:var(--warn);margin-top:2px}
.hrows{display:grid}
.hrow{padding:9px 0;border-top:1px solid var(--grid)}
.hrow:first-child{border-top:0}
.hrow.ok{display:flex;align-items:center;gap:10px;font-size:14px}
.hrow.ok .ic{color:var(--good)}
.hrow.ok .muted{margin-left:auto;font-size:13px}
.hrow.open{display:grid;gap:6px;padding:14px 16px;margin:6px 0;border:1px solid var(--ring);border-radius:10px;background:var(--wash)}
.hrow.open+.hrow.open{margin-top:6px}
.hh{display:flex;align-items:center;gap:8px}
.hrow.warn .ic{color:var(--warn)}
.hrow.bad .ic{color:var(--bad)}
.hrow.neutral .ic{color:var(--ink2)}
.hrow.open p{font-size:14px;color:var(--ink2)}
.fix{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px}
.fix .muted{font-size:12px}
.hline{margin-top:12px;font-size:13px}
.hline+.hline{margin-top:2px}
@media (max-width:760px){.gloss dl{grid-template-columns:1fr;gap:2px}.gloss dd{margin-bottom:10px}.hero{grid-column:auto}main{padding:24px 14px 48px}.card{padding:18px 16px 14px}.hero .fig{font-size:46px}.hrow.ok{flex-wrap:wrap}.hrow.ok .muted{margin-left:26px;width:100%}}
@media print{.tip{display:none}.info{display:none}}
"""
)

THEME_JS = (
    "try{var m=/[?&]theme=(dark|light)/.exec(location.search);"
    "if(m)document.documentElement.dataset.theme=m[1]}catch(e){}"
)

JS = r"""
(function(){
var d=document,tip=d.createElement('div');tip.className='tip';tip.hidden=true;d.body.appendChild(tip);
var cur=null;
function show(el,x,y){
var j;try{j=JSON.parse(el.getAttribute('data-tip'))}catch(e){return}
tip.textContent='';
var h=d.createElement('div');h.className='th';h.textContent=j.t;tip.appendChild(h);
j.r.forEach(function(r){
var row=d.createElement('div');row.className='tr';
if(r[2]!=='transparent'){var k=d.createElement('i');k.style.background=r[2];row.appendChild(k)}
else row.className='tr plain';
var n=d.createElement('span');n.textContent=r[0];row.appendChild(n);
if(r[1]){var v=d.createElement('b');v.textContent=r[1];row.appendChild(v)}
tip.appendChild(row)});
tip.hidden=false;cur=el;
var w=tip.offsetWidth,hh=tip.offsetHeight,vw=d.documentElement.clientWidth;
var left=x+14;if(left+w>vw-8)left=x-w-14;if(left<8)left=8;
var top=y-hh-14;if(top<8)top=y+18;
tip.style.left=left+'px';tip.style.top=top+'px';
var svg=el.ownerSVGElement,xh=svg&&svg.querySelector('.xh');
if(xh&&el.dataset.x){xh.setAttribute('x1',el.dataset.x);xh.setAttribute('x2',el.dataset.x);xh.removeAttribute('hidden')}}
function hide(){tip.hidden=true;cur=null;d.querySelectorAll('.xh').forEach(function(l){l.setAttribute('hidden','')})}
d.addEventListener('pointermove',function(e){
var el=e.target.closest&&e.target.closest('[data-tip]');
if(el)show(el,e.clientX,e.clientY);else if(cur)hide()});
d.addEventListener('pointerleave',hide);
d.addEventListener('focusin',function(e){
var el=e.target.closest&&e.target.closest('[data-tip]');
if(el){var b=el.getBoundingClientRect();show(el,b.left+b.width/2,b.top+b.height/2)}});
d.addEventListener('focusout',hide);
d.addEventListener('beforeprint',function(){d.querySelectorAll('details.gloss').forEach(function(g){g.open=true})});
var m=/[?&]tip=(\w+)/.exec(location.search);
if(m){var o=d.querySelector('.info[data-id="'+m[1]+'"]');if(o){var b=o.getBoundingClientRect();show(o,b.left+b.width/2,b.top+b.height)}}
d.addEventListener('keydown',function(e){if(e.key==='Escape')hide()});
})();
"""
