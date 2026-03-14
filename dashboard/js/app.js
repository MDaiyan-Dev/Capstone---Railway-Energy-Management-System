const els={
  mapSvg:byId('mapSvg'), tip:byId('tip'), statusbar:byId('statusbar'), statusText:byId('statusText'),
  kpi:{demand:byId('kpi-demand'),grid:byId('kpi-grid'),regen:byId('kpi-regen'),volt:byId('kpi-volt')},
  tiles:{
    load:byId('tile-load'),
    soc:byId('tile-soc'),
    regen:byId('tile-regen'),
    alarms:byId('tile-alarms'),
    storage:byId('tile-storage')
  },
  eventList:byId('eventList'), evtCounts:byId('evtCounts'),
  slider:byId('slider'), speed:byId('speed'), timeLabel:byId('timeLabel'),
  playhead:byId('playhead'), ticks:byId('ticks'),
  simBadge:byId('simBadge'), toggleSim:byId('toggleSim'),
  dataBase:byId('dataBase'), simBase:byId('simBase'), runId:byId('runId'),
  btnFetchLive:byId('btnFetchLive'), btnFetchRun:byId('btnFetchRun'),
  btnDemoLive:byId('btnDemoLive'), btnDemoRun:byId('btnDemoRun'),
  pickLive:byId('pickLive'), pickRun:byId('pickRun'),
  lastUpdate:byId('lastUpdate')
};
let runData=null, playTimer=null, tIndex=0, lastLiveAt=null, prevEventKeys=new Set();
function byId(id){return document.getElementById(id)}
function fmtPct(x){return (x*100).toFixed(1)+'%'}
function bestColor(state){ if(state==='red')return '#ed8796'; if(state==='amber')return '#f8bd60'; if(state==='green')return '#2e7d32'; return '#7f849c'}

// subtitle elements under the tiles (no HTML change needed)
els.tiles.loadTrend = document.querySelector('.tile[data-key="tractionLoad"] .small:nth-child(3)');
els.tiles.socFlow   = document.querySelector('.tile[data-key="batterySOC"] .small:nth-child(3)');
els.tiles.regenInfo = document.querySelector('.tile[data-key="regenToday"] .small:nth-child(3)');

// track previous numeric values so we can compute trends
let prevTileState = {
  load: null,   // W
  soc:  null,   // %
  regen: null   // kWh
};

// parse first number from a string like "1570344 W" or "42%"
function parseNumber(str){
  if(!str) return NaN;
  const m = String(str).match(/-?\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : NaN;
}

const ACK_KEY='rems_ack_v1'; let ackSet=new Set();
try{ ackSet=new Set(JSON.parse(localStorage.getItem(ACK_KEY)||'[]')) }catch(_){}
function saveAck(){ try{ localStorage.setItem(ACK_KEY, JSON.stringify([...ackSet])) }catch(_){ } }
function evKey(ev){ return ev?.id || (ev?.ts ? (ev.text||'')+'@'+ev.ts : (ev?.text||'')) }

function renderKPIs(k){
  if(!k)return;
  els.kpi.demand.textContent=fmtPct(k.demandServed??0);
  els.kpi.grid.textContent=fmtPct(k.gridDependence??0);
  els.kpi.regen.textContent=fmtPct(k.regenUtilization??0);
  els.kpi.volt.textContent=fmtPct(k.voltageDeviation??0);

  // Voltage deviation colouring: green / amber / red
  const vd = k.voltageDeviation ?? 0;
  if(vd > 0.04){
    els.kpi.volt.style.color = '#ed8796'; // high deviation
  }else if(vd > 0.03){
    els.kpi.volt.style.color = '#f8bd60'; // moderate
  }else{
    els.kpi.volt.style.color = '#a6da95'; // low deviation
  }
}

function renderTiles(t){
  if(!t)return;
  els.tiles.load.textContent=t.tractionLoad??'empty';
  els.tiles.soc.textContent=t.batterySOC??'empty';
  els.tiles.regen.textContent=t.regenToday??'empty';
  els.tiles.alarms.textContent=t.activeAlarms??'empty';
  if(els.tiles.storage) els.tiles.storage.textContent = t.storageShare ?? 'empty';

  // numeric versions
  const loadVal  = parseNumber(t.tractionLoad);
  const socVal   = parseNumber(t.batterySOC);
  const regenVal = parseNumber(t.regenToday);

  // --- mini trend for traction load ---
  if(els.tiles.loadTrend){
    let trend = 'mini trend: steady';
    if(!isNaN(loadVal) && prevTileState.load != null){
      const up = prevTileState.load * 1.02;
      const down = prevTileState.load * 0.98;
      if(loadVal > up) trend = 'mini trend: rising';
      else if(loadVal < down) trend = 'mini trend: falling';
    }
    els.tiles.loadTrend.textContent = trend;
  }

  // --- charge / discharge label based on SoC movement ---
  if(els.tiles.socFlow){
    let flow = 'charge or discharge: idle';
    if(!isNaN(socVal) && prevTileState.soc != null){
      if(socVal > prevTileState.soc + 0.1)      flow = 'charge or discharge: charging';
      else if(socVal < prevTileState.soc - 0.1) flow = 'charge or discharge: discharging';
    }
    els.tiles.socFlow.textContent = flow;
  }

  // --- regen tile subtitle ---
  if(els.tiles.regenInfo){
    let info;
    if(isNaN(regenVal) || regenVal <= 0.001){
      info = 'since 00:00: no regen yet';
    }else{
      if(regenVal < 0.5)       info = 'since 00:00: low recovered energy';
      else if(regenVal < 2.0)  info = 'since 00:00: moderate recovered energy';
      else                     info = 'since 00:00: high recovered energy';
    }
    els.tiles.regenInfo.textContent = info;
  }

  // update previous numeric state
  if(!isNaN(loadVal))  prevTileState.load = loadVal;
  if(!isNaN(socVal))   prevTileState.soc  = socVal;
  if(!isNaN(regenVal)) prevTileState.regen = regenVal;

  // Alarms tile highlighting
  const alarmsCount = Number(t.activeAlarms ?? 0);
  const alarmsTile = els.tiles.alarms && els.tiles.alarms.parentElement;
  if(alarmsTile){
    if(alarmsCount > 0){
      alarmsTile.style.borderColor = '#ed8796';
      alarmsTile.style.boxShadow = '0 0 10px rgba(237,135,150,0.7)';
    }else{
      alarmsTile.style.borderColor = '';
      alarmsTile.style.boxShadow = '';
    }
  }
}

function renderEvents(arr){
  els.eventList.innerHTML='';
  let active=0, acked=0;
  (arr||[]).forEach(ev=>{
    const key=evKey(ev);
    const isAck=ackSet.has(key);
    if(isAck) acked++; else active++;

    const row=document.createElement('div');
    row.className='event';
    if(!prevEventKeys.has(key)) row.classList.add('flash');

    const pill=document.createElement('span');
    pill.className='pill';
    pill.style = sevStyle(ev.severity);
    pill.textContent = ev.severity || 'info';

    const textDiv=document.createElement('div');
    textDiv.textContent = (ev.text||'empty') + (ev.action?(' action: '+ev.action):'');

    const btn=document.createElement('button');
    btn.textContent = isAck ? 'Unacknowledge' : 'Acknowledge';
    if(isAck) btn.classList.add('acked');

    btn.onclick=(e)=>{
      const currentlyAck = ackSet.has(key);
      if(currentlyAck){
        ackSet.delete(key);
        e.target.textContent='Acknowledge';
        e.target.classList.remove('acked');
      }else{
        ackSet.add(key);
        e.target.textContent='Unacknowledge';
        e.target.classList.add('acked');
      }
      saveAck();
      updateEventCounts();
    };

    row.appendChild(pill);
    row.appendChild(textDiv);
    row.appendChild(btn);
    els.eventList.appendChild(row);

    if(!prevEventKeys.has(key)){
      row.scrollIntoView({behavior:'smooth',block:'nearest'});
    }
    prevEventKeys.add(key);
  });
  updateEventCounts(active, acked);
}

function sevStyle(s){
  if(s==='alarm')return 'background:rgba(237,135,150,.15);color:#ffc9d1;border-color:#ed8796';
  if(s==='warn')return 'background:rgba(248,189,96,.15);color:#fee0b1;border-color:#f8bd60';
  return 'background:rgba(139,213,202,.12);color:#d2fff7;border-color:#8bd5ca';
}

function updateEventCounts(active, acked){
  if(active==null || acked==null){
    const btns=[...els.eventList.querySelectorAll('button')];
    acked=btns.filter(b=>b.classList.contains('acked')).length;
    active=btns.length-acked;
  }
  els.evtCounts.textContent=`Active ${active} | Acked ${acked}`;
}

function clearSites(){[...els.mapSvg.querySelectorAll('.site-dot,.site-label')].forEach(n=>n.remove())}
function stateGlow(s){
  const c=bestColor(s);
  if(s==='red')return `drop-shadow(0 0 8px ${c})`;
  if(s==='amber')return `drop-shadow(0 0 6px ${c})`;
  return 'none';
}

function renderAssets(assets){
  clearSites();
  let worst='green';
  const rank={red:3, amber:2, green:1, grey:0};
  (assets||[]).forEach(a=>{
    const x=80+(920-80)*(a.pos??0.5), y=210;
    let node;
    if(a.type==='bess'){
      node=document.createElementNS('http://www.w3.org/2000/svg','rect');
      node.setAttribute('x',x-20); node.setAttribute('y',y-16);
      node.setAttribute('width',40); node.setAttribute('height',32); node.setAttribute('rx',6);
    }else{
      node=document.createElementNS('http://www.w3.org/2000/svg','circle');
      node.setAttribute('cx',x); node.setAttribute('cy',y);
      node.setAttribute('r', a.type==='substation'?18:14);
    }
    node.setAttribute('class','site-dot');
    node.setAttribute('data-type', a.type||'');
    node.style.stroke = bestColor(a.state);
    node.style.filter = stateGlow(a.state);
    if(a.state==='red') node.classList.add('blink');
    els.mapSvg.appendChild(node);

    const lbl=document.createElementNS('http://www.w3.org/2000/svg','text');
    lbl.setAttribute('x',x); lbl.setAttribute('y',248); lbl.setAttribute('class','site-label');
    lbl.textContent=a.label||a.id; els.mapSvg.appendChild(lbl);

    if(rank[a.state]>rank[worst]) worst=a.state;

    const soc = (a.soc!=null)?`SoC ${a.soc}`:'';
    const vDev = (a.vDev!=null)?`V dev ${a.vDev}`:'';
    const extra = a.type==='bess'?soc:(a.type==='substation'?vDev:'');
    node.addEventListener('mouseenter',()=>{ showTip(`<strong>${a.label||a.id}</strong><br>State: ${a.state||'n/a'}${extra?('<br>'+extra):''}<br>Reason: ${a.reason||'empty'}<br>Time: ${a.ts||'empty'}`) });
    node.addEventListener('mouseleave',hideTip);
  });

  setStatusbar(worst);
  els.mapSvg.onmousemove=e=>{
    const b=els.mapSvg.getBoundingClientRect();
    els.tip.style.left=(e.clientX-b.left)+'px';
    els.tip.style.top=(e.clientY-b.top)+'px';
  };
  els.mapSvg.onmouseleave=hideTip;
}

function setStatusbar(worst){
  const c = bestColor(worst);
  els.statusbar.style.background = c; els.statusbar.style.opacity = .35;
  els.statusText.textContent = worst;
}
function showTip(html){ els.tip.innerHTML=html; els.tip.style.opacity=1 }
function hideTip(){ els.tip.style.opacity=0 }

function applyLivePayload(p){
  renderKPIs(p.kpi);
  renderTiles(p.tiles);
  renderEvents(p.events);
  renderAssets(p.status?.assets);
  els.timeLabel.textContent='Time: live';
  lastLiveAt = Date.now();
}

function applyRun(run){
  runData=run;
  const pts=run?.timeline?.points||[];
  els.slider.max=Math.max(0,pts.length-1);
  setIndex(0);
  byId('simBadge').style.display='inline-block';
  renderTicks();
}

function setIndex(i){
  if(!runData) return;
  const pts=runData.timeline.points;
  i=Math.max(0,Math.min(i,pts.length-1));
  tIndex=i; els.slider.value=String(i);
  const pt=pts[i];
  renderKPIs(pt.kpi);
  renderAssets(pt.assets);
  renderEvents(pt.events);
  if(pt.extra) renderTiles(pt.extra);
  els.timeLabel.textContent=`Time: t = ${pt.t}s (run ${runData.meta?.runId||'unknown'})`;
  updatePlayhead();
}

function updatePlayhead(){
  const w=els.slider.getBoundingClientRect().width;
  const max=Number(els.slider.max)||1, val=Number(els.slider.value)||0;
  const x = (val/max) * w;
  els.playhead.style.left = x+'px';
  els.playhead.textContent = `t = ${runData ? runData.timeline.points[val].t : 0}s`;
}

function renderTicks(){
  els.ticks.innerHTML='';
  if(!runData) return;
  const pts=runData.timeline.points;
  const w=els.slider.getBoundingClientRect().width;
  const max=pts.length-1;
  pts.forEach((pt,idx)=>{
    if(pt.events && pt.events.length){
      const d=document.createElement('div'); d.className='tick alarm';
      d.style.left = ((idx/max)*w)+'px';
      d.title = pt.events.map(e=>e.text).join(' | ');
      els.ticks.appendChild(d);
    }
  });
}

function play(){
  if(playTimer||!runData) return;
  playTimer=setInterval(()=>{
    const step=Number(els.speed.value)||1;
    if(tIndex>=runData.timeline.points.length-1){pause();return;}
    setIndex(tIndex+step);
  }, 400);
}
function pause(){ clearInterval(playTimer); playTimer=null }

els.slider.oninput=e=>{ setIndex(Number(e.target.value||0)); updatePlayhead(); };
byId('play').onclick=play;
byId('pause').onclick=pause;
byId('step').onclick=()=>setIndex(tIndex+1);
byId('toggleSim').onchange=e=>byId('simBadge').style.display=e.target.checked?'inline-block':'none';

async function fetchJSON(url){
  const r=await fetch(url);
  if(!r.ok)throw new Error('HTTP '+r.status);
  return r.json();
}
function pickAndRead(input, cb){
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if(!file) return;
    try{
      cb(JSON.parse(await file.text()));
    } catch(e){
      alert('Invalid JSON');
    }
    input.value='';
  };
  input.click();
}

byId('btnFetchLive').onclick=async()=>{
  try{
    const base=els.dataBase.value.trim();
    if(!base) return alert('Set Data API base');
    const p=await fetchJSON(base.replace(/\/$/,'')+'/snapshot');
    applyLivePayload(p);
  }
  catch(e){
    alert('Live fetch failed: '+e.message);
  }
};

async function fetchRunFromInputs(){
  try{
    const base=els.simBase.value.trim(), id=els.runId.value.trim();
    if(!base||!id) return alert('Set Simulator base and Run ID');
    const run=await fetchJSON(base.replace(/\/$/,'')+'/runs/'+encodeURIComponent(id));
    applyRun(run);
  }
  catch(e){
    alert('Run fetch failed: '+e.message);
  }
}
byId('btnFetchRun').onclick=fetchRunFromInputs;

byId('btnDemoLive').onclick=async()=>{
  try{
    const p=await fetchJSON('data/live_demo.json');
    applyLivePayload(p);
  } catch(_){
    pickAndRead(els.pickLive, applyLivePayload);
  }
};

byId('btnDemoRun').onclick=async()=>{
  try{
    const r=await fetchJSON('data/run_demo.json');
    applyRun(r);
  } catch(_){
    pickAndRead(els.pickRun, applyRun);
  }
};

setInterval(()=>{
  if(!lastLiveAt){
    els.lastUpdate.textContent = 'Last update: none';
    return;
  }
  const secs = Math.floor((Date.now()-lastLiveAt)/1000);
  els.lastUpdate.textContent = `Last update: ${secs}s ago`;
}, 1000);

const focusMap = {
  batterySOC: (a)=>a.type==='bess',
  tractionLoad: (a)=>a.type==='substation',
  regenToday: (a)=>a.type==='station',
  activeAlarms: (a)=>a.state && a.state!=='green',
  storageShare: (a)=>a.type==='bess'
};

function highlightTargets(pred){
  const nodes=[...els.mapSvg.querySelectorAll('.site-dot')];
  nodes.forEach(n=>n.style.transform='');
  const targets=nodes.filter(n=>pred({type:n.getAttribute('data-type'), state:(n.style.stroke||'')}));
  targets.forEach(t=>{ t.style.transform='scale(1.25)'; });
  setTimeout(()=>targets.forEach(t=>t.style.transform=''), 1600);
}

[...document.querySelectorAll('.tile')].forEach(tile=>{
  tile.addEventListener('click',()=>{
    const pred=focusMap[tile.dataset.key];
    if(pred) highlightTargets(pred);
  });
});

function bootstrapFromQueryParams(){
  const params = new URLSearchParams(window.location.search);
  const runId = params.get('runId');
  const dataBase = params.get('dataBase');
  const simBase = params.get('simBase');

  if(dataBase) els.dataBase.value = dataBase;
  if(simBase) els.simBase.value = simBase;
  if(runId) els.runId.value = runId;

  const hasRunAutoParams = Boolean(runId || dataBase || simBase);
  if(!hasRunAutoParams) return false;

  const onceKey = 'rems_auto_fetch_run_once:' + window.location.search;
  if(sessionStorage.getItem(onceKey)) return true;
  sessionStorage.setItem(onceKey, '1');

  setTimeout(()=>{ fetchRunFromInputs(); }, 0);
  return true;
}

const didAutoBootstrap = bootstrapFromQueryParams();
if(!didAutoBootstrap){
  fetchJSON('data/live_demo.json').then(applyLivePayload).catch(()=>{});
}
