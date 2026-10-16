'use strict';
(() => {
  const el = id => document.getElementById(id);
  const fmt = (number, digits = 1) => Number(number).toLocaleString('cs-CZ', {maximumFractionDigits: digits});
  const time = ts => new Date(ts * 1000).toLocaleTimeString('cs-CZ', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const bytes = count => {
    if (count == null || !Number.isFinite(Number(count))) return '—';
    const units = ['B','KB','MB','GB','TB']; let n = Math.abs(Number(count)), idx = 0;
    while (n >= 1024 && idx < units.length-1) { n /= 1024; idx++; }
    return fmt(n, idx === 0 ? 0 : 1) + ' ' + units[idx];
  };
  const rate = n => bytes(n) + '/s';
  const upTime = s => {const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return `${d} d ${h} h ${m} min`;};
  const element = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className=className; if(text!==undefined) node.textContent=text; return node;};
  const replace = (id, children) => el(id).replaceChildren(...children);
  const badge = (id, value, warning, critical) => {
    const node=el(id);
    const isCritical = Number.isFinite(value) && value >= critical;
    const isWarning = Number.isFinite(value) && value >= warning;
    node.className = 'small-status ' + (isCritical ? 'critical' : isWarning ? 'warning' : 'normal');
    node.textContent = isCritical ? 'Kritické' : isWarning ? 'Zvýšené' : 'V normě';
  };
  const progress = (id, value) => {el(id).style.width=Math.min(100,Math.max(0,value||0))+'%';};
  let selectedRange = '24h', historyPoints = [], refreshEvery = 20, lastUpdate = 0;
  function updateLive(data) {
    const s=data.sample;
    lastUpdate=s.ts; refreshEvery=data.refresh_interval||20;
    el('refreshLabel').textContent=String(refreshEvery);
    el('liveStatus').textContent='Systém online'; el('liveDot').classList.remove('offline');
    el('lastUpdated').textContent=time(s.ts); el('sideHostname').textContent=s.hostname;
    el('cpuVal').textContent=fmt(s.cpu.percent);
    el('cpuCaption').textContent=`${s.cpu.cores.length} jader procesoru`;
    progress('cpuBar',s.cpu.percent); badge('cpuBadge',s.cpu.percent,75,90);
    el('ramVal').textContent=fmt(s.memory.percent);
    el('ramCaption').textContent=`${bytes(s.memory.used)} / ${bytes(s.memory.total)}`;
    progress('ramBar',s.memory.percent); badge('ramBadge',s.memory.percent,75,90);
    const t=s.temperature;
    el('tempVal').textContent=t==null?'—':fmt(t);
    el('tempCaption').textContent=t==null?'Senzor nedostupný':'Teplota SoC';
    progress('tempBar',t); badge('tempBadge',t,70,80);
    el('loadVal').textContent=fmt(s.cpu.load[0],2);
    el('coreCount').textContent=String(s.cpu.cores.length);
    progress('loadBar',(s.cpu.load[0]/Math.max(1,s.cpu.cores.length))*100);
    badge('loadBadge',s.cpu.load[0],Math.max(1,s.cpu.cores.length)*.75,Math.max(1,s.cpu.cores.length));
    el('uptime').textContent=upTime(s.uptime_seconds);
    el('systemText').textContent=s.system;
    el('memoryTotal').textContent=bytes(s.memory.total);
    el('memoryAvailable').textContent=bytes(s.memory.available);
    el('swapText').textContent=s.swap.total ? `${bytes(s.swap.total)} (${fmt(s.swap.percent)} %)` : 'Vypnutý';
    el('networkDown').textContent=rate(s.network.down);
    el('networkUp').textContent=rate(s.network.up);

    const coreNodes = s.cpu.cores.map((pct,i)=>{
      const row=element('div','core-row'); const top=element('div','core-row-top');
      top.append(element('span','',`Jádro ${i+1}`),element('strong','',`${fmt(pct)} %`));
      const bar=element('div','core-track'); const fill=element('div','core-fill'); fill.style.width=`${Math.min(100,Math.max(0,pct))}%`;bar.append(fill);row.append(top,bar);return row;
    });
    replace('coreRows',coreNodes);

    const diskNodes=s.disks.map(d=>{
      const row=element('div','disk-row'); const top=element('div','disk-top');const meta=element('div');
      meta.append(element('div','disk-name',d.path==='/'?'Systémový disk':d.path),element('div','disk-caption',`${bytes(d.used)} z ${bytes(d.total)} · volné ${bytes(d.free)}`));
      top.append(meta,element('strong','disk-percent',`${fmt(d.percent)} %`));
      const bar=element('div','disk-track');const fill=element('div','disk-fill '+(d.percent>=90?'critical':d.percent>=80?'warning':''));fill.style.width=d.percent+'%';bar.append(fill);
      row.append(top,bar);return row;
    });
    replace('diskRows',diskNodes.length?diskNodes:[element('p','muted','Není dostupný žádný disk.')]);

    const containers=s.containers||[];
    el('dockerCount').textContent=String(containers.length);
    el('dockerRunning').textContent=String(containers.length);
    el('dockerError').hidden=!s.docker_error;
    if(s.docker_error)el('dockerError').textContent=s.docker_error;
    const containerNodes=containers.map(c=>{
      const tr=element('tr');
      const tdName=element('td');tdName.append(element('span','container-name',c.name));
      const tdState=element('td');tdState.append(element('span','container-status',c.status));
      const tdCpu=element('td');tdCpu.append(element('strong','',fmt(c.cpu,2)+' %'));
      const meter=element('div','cpu-meter');const fill=element('div','cpu-meter-fill');fill.style.width=Math.min(100,c.cpu)+'%';meter.append(fill);tdCpu.append(meter);
      tr.append(tdName,tdState,tdCpu,element('td','',c.memory),element('td','',c.pids));return tr;
    });
    if(!containerNodes.length){const tr=element('tr');const td=element('td','empty-row',s.docker_error?'Údaje Dockeru nejsou dostupné.':'Žádné běžící kontejnery.');td.colSpan=5;tr.append(td);containerNodes.push(tr);}
    replace('dockerBody',containerNodes);

    const alerts=data.alerts||[];
    const alertLabel=el('alertsCount');alertLabel.textContent=alerts.length?`${alerts.length} upozornění`:'Vše v pořádku';
    alertLabel.className='small-status '+(alerts.length?'warning':'normal');
    if(!alerts.length){const ok=element('div','all-good');ok.append(element('span','', '✓'));
      const msg=element('div');msg.append(element('strong','','Žádné aktivní upozornění'),element('span','','Všechny sledované hodnoty jsou v nastaveném rozmezí.'));ok.append(msg);replace('activeAlerts',[ok]);
    } else {replace('activeAlerts',alerts.map(a=>{const row=element('div','alert-item');row.append(element('strong','',a.title),element('span','',a.detail));return row;}));}
    const events=data.recent_events||[];
    replace('eventsList',events.length?events.map(e=>{
      const row=element('div','event-row');row.append(element('span','event-icon'+(e.active?'':' resolved'),e.active?'!':'✓'));
      const body=element('div','event-body');body.append(element('strong','',e.title),element('span','',e.detail));
      row.append(body,element('span','event-time',time(e.ts)));return row;
    }):[element('div','muted','Zatím nebyla zaznamenána žádná výstraha.')]);
  }

  function drawChart() {
    const canvas=el('historyChart'), frame=canvas.parentElement,rect=frame.getBoundingClientRect();
    const width=Math.max(120,rect.width),height=Math.max(120,rect.height),ratio=Math.min(2,window.devicePixelRatio||1);
    canvas.width=Math.floor(width*ratio);canvas.height=Math.floor(height*ratio);
    const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
    const points=historyPoints;
    el('chartEmpty').hidden=points.length>0;
    const pad={left:33,right:10,top:14,bottom:28};const plotWidth=width-pad.left-pad.right,plotHeight=height-pad.top-pad.bottom;
    const styles=getComputedStyle(document.documentElement);
    ctx.font='10px DM Sans, Arial, sans-serif';ctx.fillStyle='#7889a1';ctx.lineWidth=1;
    for (let step=0;step<=4;step++) {
      const y=pad.top+(step/4)*plotHeight;
      ctx.strokeStyle='#26364a';ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(width-pad.right,y);ctx.stroke();
      ctx.textAlign='right';ctx.fillText(`${100-step*25}`,pad.left-9,y+3);
    }
    if(!points.length)return;
    const min=points[0].ts,max=points[points.length-1].ts,spread=Math.max(1,max-min);
    const x=ts=>pad.left+(ts-min)/spread*plotWidth;
    const y=value=>pad.top+(1-Math.max(0,Math.min(100,value??0))/100)*plotHeight;
    const series=[['cpu','#67d5ec'],['ram','#6d8ff3'],['temperature','#bb91f1']];
    for(const [key,color] of series){
      let moved=false;ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=2;ctx.lineJoin='round';
      for(const p of points){if(p[key] == null){moved=false;continue;}const px=x(p.ts),py=y(p[key]);if(!moved){ctx.moveTo(px,py);moved=true;}else{ctx.lineTo(px,py);}}
      ctx.stroke();
    }
    for(let i=0;i<=4;i++){
      const ts=min+(spread*i/4),px=x(ts);
      const options=selectedRange==='7d'?{day:'2-digit',month:'2-digit'}:{hour:'2-digit',minute:'2-digit'};
      const label=new Date(ts*1000).toLocaleString('cs-CZ',options);
      ctx.fillStyle='#8293a7';ctx.textAlign=i===0?'left':i===4?'right':'center';ctx.fillText(label,px,height-5);
    }
  }
  async function loadHistory(){
    try{
      const response=await fetch(`/api/history?range=${encodeURIComponent(selectedRange)}`,{cache:'no-store'});
      if(response.status===401){location.assign('/login');return;}
      if(!response.ok)throw new Error('historie');
      historyPoints=(await response.json()).points||[];
      drawChart();
    }catch(err){ el('chartEmpty').hidden=false;el('chartEmpty').textContent='Historie není momentálně dostupná.';}
  }
  async function loadLive(){
    try{
      const response=await fetch('/api/live',{cache:'no-store'});
      if(response.status===401){location.assign('/login');return;}
      if(!response.ok)throw new Error('Žádná aktuální data');
      updateLive(await response.json());
    }catch(err){
      el('liveStatus').textContent='Data nedostupná';el('liveDot').classList.add('offline');
      if(lastUpdate)el('lastUpdated').textContent=`Poslední data ${time(lastUpdate)}`;
    }
  }
  document.querySelectorAll('.range-btn').forEach(button=>button.addEventListener('click',()=>{
    selectedRange=button.dataset.range;
    document.querySelectorAll('.range-btn').forEach(x=>x.classList.toggle('selected',x===button));
    loadHistory();
  }));
  document.querySelectorAll('.nav-link').forEach(link=>link.addEventListener('click',()=>{
    document.querySelectorAll('.nav-link').forEach(x=>x.classList.toggle('active',x===link));
  }));
  const clock=()=>{el('clock').textContent=new Date().toLocaleTimeString('cs-CZ');};
  clock();setInterval(clock,1000);
  window.addEventListener('resize',()=>drawChart());
  loadLive();loadHistory();
  setInterval(loadLive,20000);setInterval(loadHistory,60000);
})();
