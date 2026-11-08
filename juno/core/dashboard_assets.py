# ruff: noqa: E501
LIGHT = (
    "--bg:#fafafa;--panel:#fff;--ink:#171717;--muted:#737373;--line:#e5e5e5;--accent:#3b82f6;--soft:#f4f4f5;"
    "--green:#22c55e;--amber:#f59e0b;--red:#ef4444;--amber-ink:#b45309;--red-ink:#dc2626;--green-ink:#15803d"
)
DARK = (
    "--bg:#0a0a0a;--panel:#171717;--ink:#ededed;--muted:#a3a3a3;--line:#262626;--accent:#60a5fa;--soft:#1f1f1f;"
    "--green:#22c55e;--amber:#f59e0b;--red:#ef4444;--amber-ink:#fbbf24;--red-ink:#f87171;--green-ink:#4ade80"
)

CSS = (
    """
:root{color-scheme:light;"""
    + LIGHT
    + """}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;"""
    + DARK
    + """}}
:root[data-theme=dark]{color-scheme:dark;"""
    + DARK
    + """}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
main{max-width:980px;margin:0 auto;padding:24px 20px 56px}
h2{margin:0 0 12px;font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
h3{margin:20px 0 8px;font-size:13px;font-weight:600;color:var(--muted)}
h3:first-child{margin-top:0}
ul{margin:0;padding:0;list-style:none}
p{margin:6px 0}
.muted,.sub{color:var(--muted)}
.sub{font-size:13px}
.na{color:var(--muted)}
.warn{color:var(--amber-ink)}
.count{margin-left:8px;font-weight:500;letter-spacing:0;color:var(--muted)}
.cmd{display:inline-block;max-width:100%;vertical-align:middle;font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--soft);border:1px solid var(--line);padding:2px 8px;border-radius:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;user-select:all;-webkit-user-select:all;cursor:copy}
.dot{width:10px;height:10px;border-radius:50%;background:var(--muted);display:inline-block;flex:none}
.lv-good .dot,.lv-green .dot{background:var(--green)}
.lv-warn .dot,.lv-yellow .dot{background:var(--amber)}
.lv-bad .dot,.lv-red .dot{background:var(--red)}
.bd{display:inline-block;padding:1px 8px;border:1px solid var(--line);border-radius:999px;font-size:12px;line-height:18px;color:var(--muted);white-space:nowrap}
.bd.warn{color:var(--amber-ink);border-color:var(--amber)}
.bd.bad{color:var(--red-ink);border-color:var(--red)}
.bd.ok{color:var(--green-ink);border-color:var(--green)}
.bd.accent{color:var(--accent);border-color:var(--accent)}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:24px}
.state{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:600;letter-spacing:-.01em}
.state .dot{width:12px;height:12px}
.meta{display:flex;align-items:center;gap:12px;color:var(--muted);font-size:13px}
button,input{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:4px 12px}
button{cursor:pointer;font-size:13px}
button:hover{border-color:var(--muted)}
input{width:100%;padding:8px 12px;margin-bottom:12px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:24px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px}
.num{font-size:32px;line-height:1.1;font-weight:600;letter-spacing:-.02em;white-space:nowrap}
.lbl{margin-top:8px;color:var(--ink)}
.kpi .sub{min-height:20px;margin-top:2px;font-size:12px}
.todo,.sec{background:var(--panel);border:1px solid var(--line);border-radius:12px}
.todo{padding:20px 20px 12px;margin-bottom:24px}
.todo li{display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;padding:10px 0;border-top:1px solid var(--line)}
.todo li:first-child{border-top:0;padding-top:0}
.todo .tx{flex:1 1 240px}
.todo .cmd{flex:0 1 auto;max-width:46%}
.todo .more{color:var(--muted);font-size:13px}
.bds{display:flex;gap:6px}
.empty{margin:0 0 8px;color:var(--muted)}
.tx{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.acc{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
.sec>summary{display:flex;align-items:center;gap:10px;padding:16px 20px;cursor:pointer;list-style:none}
.sec>summary::-webkit-details-marker{display:none}
.sec>summary::before{content:"";width:6px;height:6px;border:solid var(--muted);border-width:0 1.5px 1.5px 0;transform:rotate(-45deg);transition:transform .15s;flex:none}
.sec[open]>summary::before{transform:rotate(45deg)}
.st{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}
.ss{margin-left:auto;color:var(--muted);font-size:13px;white-space:nowrap}
.sb{padding:4px 20px 20px;border-top:1px solid var(--line);padding-top:16px}
.grp+.grp{margin-top:20px}
.ghd{display:flex;align-items:center;gap:10px;margin-bottom:4px}
.ghd .bd{margin-left:auto}
.sb li{display:flex;align-items:center;gap:12px;padding:6px 0;border-top:1px solid var(--line)}
.sb li:first-child{border-top:0}
.sb li .tx{flex:1}
.late-row{box-shadow:inset 2px 0 0 var(--red);padding-left:10px!important}
.when{flex:none;width:96px}
table{width:100%;border-collapse:collapse}
td,th{padding:8px 12px 8px 0;border-top:1px solid var(--line);vertical-align:middle;text-align:left}
tr:first-child td,tr:first-child th{border-top:0}
th{font-weight:500;color:var(--muted);font-size:12px}
.nm{white-space:nowrap}
.nm .lv{margin-right:10px}
.fx{text-align:right;max-width:280px}
.ab td:first-child{font-weight:600}
.cov{display:flex;justify-content:space-between;margin:20px 0 4px}
.meter{height:6px;background:var(--soft);border-radius:3px;overflow:hidden;margin:4px 0 8px}
.meter i{display:block;height:100%;background:var(--accent)}
.meter.warn i{background:var(--amber)}
.sub details,details.sub{margin-top:16px}
details.sub summary{cursor:pointer;color:var(--muted);font-size:13px}
details.sub ul{max-height:220px;overflow:auto;margin-top:8px}
details.sub li{display:block;padding:4px 0;font-size:13px;color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:16px}
.stat{background:var(--soft);border-radius:10px;padding:14px 16px}
.stat b{display:block;font-size:22px;font-weight:600;letter-spacing:-.01em;white-space:nowrap}
.stat span{display:block;color:var(--ink);font-size:13px}
.stat em{display:block;color:var(--muted);font-size:12px;font-style:normal;min-height:18px}
.stat.over b{color:var(--red-ink)}
svg{display:block;width:100%;height:auto;margin-bottom:8px}
svg text{fill:var(--muted);font:11px system-ui,sans-serif}
svg text.val{fill:var(--ink);font-weight:600}
rect.bar{fill:var(--accent)}.base{stroke:var(--line);stroke-width:1}
@media (max-width:760px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.stats{grid-template-columns:minmax(0,1fr)}}
@media (max-width:480px){main{padding:16px 12px 40px}.kpi{padding:14px}.num{font-size:28px}.fx{display:none}.when{width:auto}.todo .cmd{max-width:100%}}
"""
)

THEME_JS = (
    "try{var m=/[?&]theme=(dark|light)/.exec(location.search),t=m?m[1]:localStorage.getItem('juno-theme');"
    "if(t)document.documentElement.dataset.theme=t}catch(e){}"
)

JS = """
(function(){
var d=document,r=d.documentElement,q=d.getElementById('q'),b=d.getElementById('theme');
if(q){q.parentNode.hidden=false;q.addEventListener('input',function(){
var t=q.value.trim().toLowerCase();
d.querySelectorAll('li[data-f]').forEach(function(e){e.hidden=!!t&&e.dataset.f.indexOf(t)<0});
d.querySelectorAll('.grp').forEach(function(g){g.hidden=!!t&&!g.querySelector('li:not([hidden])')})})}
if(b){b.hidden=false;b.addEventListener('click',function(){
var dark=r.dataset.theme?r.dataset.theme==='dark':matchMedia('(prefers-color-scheme:dark)').matches;
var n=dark?'light':'dark';r.dataset.theme=n;try{localStorage.setItem('juno-theme',n)}catch(e){}})}
})();
"""
