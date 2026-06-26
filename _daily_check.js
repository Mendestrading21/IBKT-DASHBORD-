
function q(id){return document.getElementById(id)}
function clr(s){return s>=72?'#F5A623':s>=55?'#FFB23F':'#EF4444'}
function gcls(g){return g==='S+'||g==='S'?'g-sp':g==='A'?'g-a':g==='B'?'g-b':'g-c'}
function bcls(v){return v==='BUY'?'b-achat':(v==='WATCH'||v==='WAIT')?'b-surv':'b-evit'}
function vfr(v){return {BUY:'ACHAT',WATCH:'SURVEILLER',WAIT:'ATTENTE',AVOID:'ÉVITER'}[v]||v}
function rv(v){v=v||0;const c=v>=1.5?'hot':v>=1.0?'warm':'cold';return `<span class="rvol ${c}">${v.toFixed(2)}×</span>`}
function chg(c){c=c||0;return `<span class="${c>=0?'up':'dn'}">${c>=0?'+':''}${c}%</span>`}
function go(s){location.href='/analyse?sym='+s}
function er(n,t){return `<tr><td colspan="${n}" class="muted" style="text-align:center;padding:16px">${t}</td></tr>`}
let __spkN=0;
function spark(arr,w=82,h=22,days=24){
  if(!arr||arr.length<2)return '';
  const d=arr.slice(-days).filter(v=>v!=null&&!isNaN(v));if(d.length<2)return '';
  const up=d[d.length-1]>=d[0],col=up?'#F5A623':'#EF4444',gid='s'+(++__spkN);
  const mn=Math.min(...d),mx=Math.max(...d),rg=(mx-mn)||1,pad=2,iw=w-pad*2,ih=h-pad*2;
  const X=i=>pad+(i/(d.length-1))*iw,Y=v=>pad+ih-((v-mn)/rg)*ih;
  const pts=d.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)),line='M'+pts.join(' L');
  const area=line+' L'+X(d.length-1).toFixed(1)+','+(h-pad)+' L'+X(0).toFixed(1)+','+(h-pad)+' Z';
  const lx=X(d.length-1).toFixed(1),ly=Y(d[d.length-1]).toFixed(1);
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="${gid}f" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity="0.30"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><path d="${area}" fill="url(#${gid}f)"/><path d="${line}" fill="none" stroke="${col}" stroke-width="1.4" stroke-linejoin="round"/><circle cx="${lx}" cy="${ly}" r="1.8" fill="${col}"><animate attributeName="r" values="1.8;3;1.8" dur="1.6s" repeatCount="indefinite"/></circle></svg>`;
}
function bkpill(b){return `<span class="bkt bkt-${b||'long'}">${(b||'long').toUpperCase()}</span>`}
function flagpills(fl){return (fl||[]).map(f=>{const c=f.indexOf('decay')>=0?'fl-decay':f.indexOf('earn')>=0?'fl-earn':f.indexOf('IV')>=0?'fl-iv':'fl-spread';return `<span class="flagpill ${c}">${f}</span>`}).join('')}
let __donut=null,__secbar=null;
function renderCharts(d){
  if(typeof Chart==='undefined')return;
  const rows=d.rows||[];
  const nB=rows.filter(r=>r.verdict==='BUY').length,nW=rows.filter(r=>r.verdict==='WATCH'||r.verdict==='WAIT').length,nA=rows.filter(r=>r.verdict==='AVOID').length;
  const dc=q('dDonut');
  if(dc){if(__donut)__donut.destroy();__donut=new Chart(dc,{type:'doughnut',data:{labels:['ACHAT','SURV.','ÉVITER'],datasets:[{data:[nB,nW,nA],backgroundColor:['#22C55E','#F5A623','#EF4444'],borderWidth:0}]},options:{cutout:'66%',plugins:{legend:{position:'bottom',labels:{color:'#9aa7bd',font:{size:10},boxWidth:10,padding:8}}}}});}
  const secs=d.sectors||[],sb=q('dSecBar');
  if(sb&&secs.length){if(__secbar)__secbar.destroy();__secbar=new Chart(sb,{type:'bar',data:{labels:secs.map(s=>s.sector),datasets:[{data:secs.map(s=>s.avg_score),backgroundColor:secs.map(s=>s.avg_score>=72?'#F5A623':s.avg_score>=55?'#FFB23F':'#EF4444'),borderRadius:4}]},options:{indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{max:100,ticks:{color:'#5b6678',font:{size:9}},grid:{color:'#0a0a0a'}},y:{ticks:{color:'#9aa7bd',font:{size:10}},grid:{display:false}}}}});}
}
function renderSectors(d){
  const secs=d.sectors||[];
  q('dSectors').innerHTML=secs.map(s=>{
    const col=s.avg_score>=72?'#F5A623':s.avg_score>=55?'#FFB23F':'#EF4444';
    const dl=s.delta==null?'':(s.delta>=0?`<span class="dlt up">▲+${s.delta}</span>`:`<span class="dlt dn">▼${s.delta}</span>`);
    const tot=Math.max(s.n,1);
    const seg=`<div class="segbar"><div style="width:${s.n_buy/tot*100}%;background:#F5A623"></div><div style="width:${s.n_watch/tot*100}%;background:#FFB23F"></div><div style="width:${s.n_avoid/tot*100}%;background:#EF4444"></div></div>`;
    const mem=(s.members||[]).map(m=>`<span class="mem" onclick="event.stopPropagation();go('${m.symbol}')" style="color:${clr(m.score)}">${m.symbol} ${m.score}</span>`).join('');
    return `<div class="seccard" style="color:${col}">
      <div class="sh">${s.icon} ${s.sector}<span class="big" style="color:${col}">${s.avg_score}</span>${dl}</div>
      <div class="mini"><span>${s.n_buy}/${s.n} ACHAT</span><span class="${s.avg_change>=0?'up':'dn'}">${s.avg_change>=0?'+':''}${s.avg_change}%</span><span>RS ${s.avg_rs}</span><span>RVOL ${s.avg_rvol}×</span><span class="rb rb-${s.risk_band.toLowerCase()}">${s.risk_band}</span></div>
      ${seg}<div class="members">${mem}</div></div>`;
  }).join('')||'<span class="muted">secteurs en calcul…</span>';
}
function renderAnomalies(d){
  const a=d.anomalies||[];q('dAnomCnt').textContent=a.length+' SIGNAUX';
  const tint=x=>(x.dir==='WARN'||x.dir==='DOWN')?'#EF4444':x.dir==='NEUTRAL'?'#FFB23F':'#F5A623';
  q('dAnoms').innerHTML=a.length?a.slice(0,12).map(x=>{const c=tint(x);const buy=(x.dir==='UP');return `<div class="anom" onclick="go('${x.symbol}')" style="display:block;padding:11px 14px">
    <div style="display:flex;align-items:center;gap:8px">
      <span class="sym" style="min-width:50px">${x.symbol}</span>
      <span class="atag" style="color:${c};background:${c}22">${buy?'▲ ':x.dir==='DOWN'||x.dir==='WARN'?'▼ ':''}${x.label}</span>
      <span class="muted" style="flex:1;font-size:12px">${x.note}</span>
      <span class="sev" style="color:${c};font-weight:800">${x.sev}</span></div>
    ${x.interest?`<div class="muted" style="font-size:11px;margin:6px 0 0 10px;padding-left:10px;line-height:1.55;border-left:2px solid ${c}55">${buy?'💡 Pourquoi acheter : ':'⚠️ '}${x.interest}</div>`:''}</div>`}).join(''):'<div class="muted" style="padding:16px">aucune anomalie — marché calme</div>';
}

function renderDaily(d){
  const dy=d.daily||{}, sec=dy.sections||{}, DET=d.detail||{};
  const spk=s=>spark((DET[s]||{}).series&&DET[s].series.close);
  // VERDICT DU JOUR (contexte marché)
  const mc=d.market_ctx||{};
  if(q('dVerdictTxt')){
    q('dVerdictTxt').textContent=mc.verdict||'données marché en cours…';
    const tag=(lab,val,col)=>`<span style="font-size:11px;font-weight:800;letter-spacing:.5px;padding:6px 12px;border-radius:20px;background:${col}1f;color:${col};border:1px solid ${col}55">${lab} ${val}</span>`;
    const rc=mc.spy_regime==='TREND'?'#F5A623':mc.spy_regime==='CHOP'?'#EF4444':'#FFB23F';
    const ro=mc.roro==='RISK-ON'?'#F5A623':mc.roro==='RISK-OFF'?'#EF4444':'#FFB23F';
    const vc=mc.vix_band==='calme'?'#F5A623':mc.vix_band==='stress'?'#EF4444':'#FFB23F';
    q('dVerdictTags').innerHTML=(mc.spy_regime?tag('RÉGIME',mc.spy_regime==='TREND'?'TENDANCE':mc.spy_regime==='CHOP'?'RANGE':'NEUTRE',rc):'')
      +(mc.roro?tag('',mc.roro,ro):'')+(mc.vix!=null?tag('VIX',mc.vix+(mc.vix_chg!=null?` (${mc.vix_chg>=0?'+':''}${mc.vix_chg}%)`:''),vc):'');
  }
  if(d.spy)q('dSpy').innerHTML=`SPY $${d.spy.price} ${chg(d.spy.change)}`;
  const m=d.market||{};const mk=q('dMkt');mk.className='pill '+(m.open?'live':'shut');
  mk.innerHTML=`<span class="pdot"></span>${m.open?'MARCHÉ OUVERT':'MARCHÉ FERMÉ'} · ${m.et||'—'}`;
  q('dDate').innerHTML=`<span class="pdot"></span>MAJ ${d.updated||'—'}`;
  const b=(d.breadth!=null?d.breadth:(dy.meta?dy.meta.breadth:0))||0;
  q('dBreadthFill').style.width=b+'%';q('dBreadthVal').textContent=b+'%';
  const rows=d.rows||[];
  const nB=rows.filter(r=>r.verdict==='BUY').length,nW=rows.filter(r=>r.verdict==='WATCH'||r.verdict==='WAIT').length,nA=rows.filter(r=>r.verdict==='AVOID').length;
  q('dSeg').innerHTML=`<span class="gb">● ACHAT <b>${nB}</b></span><span class="gy">● SURV. <b>${nW}</b></span><span class="gr">● ÉVIT. <b>${nA}</b></span>`;

  // SETUP DU JOUR
  const pick=(sec.top_picks||[])[0], board=d.options_board||[];
  if(pick){const p=pick.plan||{};const call=board.find(c=>c.type==='CALL'&&c.sym===pick.symbol)||board.find(c=>c.type==='CALL');
    q('dHero').innerHTML=`<div class="heroL">
      <div class="hsym">${(pick.grade==='S+'||pick.grade==='S')?'🔥 ':''}${pick.symbol} <span class="grade ${gcls(pick.grade)}">${pick.grade}</span></div>
      <div class="muted" style="margin:5px 0 12px">$${pick.price} ${chg(pick.change)} · <span class="badge ${bcls(pick.verdict)}">${vfr(pick.verdict)}</span> · ${pick.sigcount}/7 signaux · RVOL ${rv(pick.rvol)}</div>
      <div class="hplan"><span>ENTRÉE <b>$${p.entry??'—'}</b></span><span class="dn">STOP <b>$${p.stop??'—'}</b></span><span class="up">TP1 $${p.tp1??'—'}</span><span class="up">TP2 $${p.tp2??'—'}</span><span class="up">TP3 $${p.tp3??'—'}</span><span>R:R <b>${p.rr??'—'}</b></span></div></div>
      <div class="heroR">${call?`<div class="muted" style="font-size:11px;letter-spacing:1px;margin-bottom:6px">💎 CALL ASSOCIÉ</div>
        <div class="hsym" style="font-size:19px">$${call.strike} <span class="muted" style="font-size:12px">${call.exp}</span></div>
        <div class="muted" style="margin-top:6px">Δ ${call.delta} · IV ${call.iv}% · <span style="color:${clr(call.suit)};font-weight:700">${call.grade}</span></div>
        <div style="margin-top:8px;font-size:21px;font-weight:800">$${(call.cost||0).toLocaleString('fr-FR')}</div>
        <div class="muted">breakeven $${call.be}</div>`:'<span class="muted">pas de call propre aujourd\'hui</span>'}</div>`;
  } else q('dHero').innerHTML='<div style="padding:22px" class="muted">Aucun BUY aujourd\'hui — marché en repli. Patience, on ne force pas.</div>';

  // CHANGES
  const ch=sec.changes||[];
  q('dChanges').innerHTML=ch.length?ch.map(c=>`<span class="chip ${c.kind}" onclick="go('${c.symbol}')"><b>${c.symbol}</b> ${c.txt}</span>`).join(''):`<span class="muted">${dy.meta&&dy.meta.has_prev?'rien de neuf depuis hier ✓':'baseline en constitution — reviens demain pour le diff'}</span>`;

  // TOP PICKS
  const tp=sec.top_picks||[];q('dPicksCnt').textContent=tp.length+' SETUPS';
  q('dPicks').innerHTML=tp.map(r=>{const p=r.plan||{};return `<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${(r.grade==='S+'||r.grade==='S')?'<span class="pep">🔥</span> ':''}${r.symbol}</span><div class="spk">${spk(r.symbol)}</div></td>
    <td>$${r.price} ${chg(r.change)}</td><td><span class="sc" style="color:${clr(r.score)}">${r.score}</span></td>
    <td><span class="grade ${gcls(r.grade)}">${r.grade}</span></td><td><span class="badge ${bcls(r.verdict)}">${vfr(r.verdict)}</span></td>
    <td class="muted">${r.sigcount}/7</td><td>${rv(r.rvol)}</td>
    <td class="sub"><span class="dn">$${p.stop??'—'}</span> · <span class="up">$${p.tp2??'—'}</span></td></tr>`}).join('')||er(8,"aucun BUY aujourd'hui — marché en repli");

  // SWING
  const sw=sec.swing_trades||[];q('dSwingCnt').textContent=sw.length+' IDÉES';
  q('dSwing').innerHTML=sw.map(r=>{const p=r.plan||{},rk=(p.entry&&p.stop)?(p.entry-p.stop).toFixed(2):'—';return `<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span><div class="sub">$${r.price} ${chg(r.change)}</div><div class="spk">${spk(r.symbol)}</div></td>
    <td><span class="grade ${gcls(r.grade)}">${r.grade}</span> <span class="sc" style="color:${clr(r.score)}">${r.score}</span></td>
    <td>${rv(r.rvol)}</td><td>$${p.entry??'—'}</td><td class="dn">$${p.stop??'—'}</td>
    <td><span class="rb rb-${(r.risk_band||'Med').toLowerCase()}">${r.risk_band||'—'}</span></td>
    <td class="up sub">$${p.tp1??'—'} · $${p.tp2??'—'} · $${p.tp3??'—'}</td><td><span class="pep">${p.rr??'—'}R</span></td></tr>`}).join('')||er(8,"pas de setup swing net (score≥65 + au-dessus MM50)");

  // LIVE MOMENTUM
  q('dMom').innerHTML=(sec.live_momentum||[]).map(r=>`<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td class="up">+${r.change}%</td>
    <td class="${r.roc>=0?'up':'dn'}">${r.roc>=0?'+':''}${r.roc}%</td><td>${Math.round(r.rsi)}</td><td>${Math.round(r.rs)}</td><td>${rv(r.rvol)}</td></tr>`).join('')||er(6,"rien en hausse aujourd'hui");

  // OPTIONS DU JOUR (court/moyen/long)
  const calls=(d.options_board||[]).filter(c=>c.type==='CALL').slice(0,12);
  q('dOptCnt').textContent=calls.length+' CONTRATS';
  q('dOpt').innerHTML=calls.length?calls.map(c=>`<tr onclick="go('${c.sym}')">
    <td><span class="sym">${c.sym}</span></td><td>${bkpill(c.bucket)}</td><td class="sub">${c.exp?c.exp.slice(8,10)+'/'+c.exp.slice(5,7):''} <span class="muted">${c.dte}j</span></td>
    <td>$${c.strike}</td>
    <td style="font-weight:700;color:${(c.pop||0)>=50?'#22C55E':(c.pop||0)>=38?'#F5A623':'#EF4444'}">${c.pop}%</td>
    <td style="font-weight:700;color:${c.danger==='Faible'?'#22C55E':c.danger==='Modéré'?'#F5A623':c.danger==='Élevé'?'#d98a52':'#EF4444'}">${c.danger}</td>
    <td>${c.delta}</td><td>$${(c.cost||0).toLocaleString('fr-FR')}</td>
    <td class="sub">si $${c.tgt} <span class="${c.pot>=0?'up':'dn'}">${c.pot>=0?'+':''}${c.pot}%</span></td>
    <td class="sub">${flagpills(c.flags)}</td></tr>`).join(''):er(10,"chaînes en calcul (~1 min)…");

  // TOP MOVERS
  q('dMovers').innerHTML=(sec.top_movers||[]).map(r=>`<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td>${chg(r.change)}</td>
    <td><span class="sc" style="color:${clr(r.score)}">${r.score}</span></td><td>${rv(r.rvol)}</td><td><span class="badge ${bcls(r.verdict)}">${vfr(r.verdict)}</span></td></tr>`).join('')||er(5,'—');

  // SECOND LEG
  q('dSecond').innerHTML=(sec.second_leg||[]).map(r=>{const p=r.plan||{};return `<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td><span class="sc" style="color:${clr(r.score)}">${r.score}</span> <span class="grade ${gcls(r.grade)}">${r.grade}</span></td>
    <td>${r.ext_atr}×</td><td>${Math.round(r.rsi)}</td><td class="sub">$${p.entry??'—'} / <span class="dn">$${p.stop??'—'}</span></td></tr>`}).join('')||er(5,"pas de reprise nette");

  // GARDE-FOUS
  q('dGuard').innerHTML=(sec.guardrails||[]).map(r=>`<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td><span class="grade ${gcls(r.grade)}">${r.grade}</span></td>
    <td>${r.rsi}</td><td>${r.ext_atr}×</td><td class="sub" style="color:#FFB23F">${(r.flags||[]).join(' · ')}</td></tr>`).join('')||er(5,"rien de surchauffé ✓");

  // À ÉVITER
  q('dAvoid').innerHTML=(sec.avoid||[]).map(r=>`<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td><span class="sc" style="color:${clr(r.score)}">${r.score}</span></td>
    <td><span class="grade ${gcls(r.grade)}">${r.grade}</span></td><td>${Math.round(r.rsi)}</td><td class="sub dn">${r.reason}</td></tr>`).join('')||er(5,"aucun titre à éviter");

  renderSectors(d); renderAnomalies(d); renderCharts(d);
  window.__lastD=d; renderPositions(d); renderHero2(d); renderRisk(d); renderIndices(d); renderInternals(d); renderRecs(d); renderMovers(d);
}
function renderInternals(d){
  const el=document.getElementById('dInternals');if(!el)return;
  const b=(d.market_ctx||{}).breadth||{};
  const r=(lab,a,bb,ca,cb)=>`<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #1a1a1a;font-size:12px"><span class="muted">${lab}</span><span><b class="${ca}">${a}</b> <span class="muted">/</span> <b class="${cb}">${bb}</b></span></div>`;
  el.innerHTML=r('Avance / Déclin',(b.adv||0),(b.dec||0),'up','dn')
   +r('Nouveaux hauts / bas 52s',(b.nh||0),(b.nl||0),'up','dn')
   +`<div style="display:flex;justify-content:space-between;padding:7px 0;font-size:12px"><span class="muted">Au-dessus MM50 / MM200</span><span><b>${b.above50!=null?b.above50:'—'}%</b> <span class="muted">/</span> <b>${b.above200!=null?b.above200:'—'}%</b></span></div>`;
}
function renderIndices(d){
  const el=document.getElementById('dIndices');if(!el)return;
  const ix=d.indices||[];
  el.innerHTML=ix.length?ix.map(i=>{const pos=i.vix?(i.change<=0):(i.change>=0);return `<div class="idx"><div class="in">${i.name}</div><div class="ip">${(i.price||0).toLocaleString('fr-FR')}</div><div class="ic ${pos?'up':'dn'}">${i.change>=0?'▲ +':'▼ '}${i.change}%</div></div>`}).join(''):'<span class="muted">indices en cours…</span>';
}
function gauge(val,label,sub,danger){
  const r=32,c=2*Math.PI*r,pct=Math.max(0,Math.min(100,val||0)),off=c*(1-pct/100);
  const col=danger?(pct>=66?'#EF4444':pct>=33?'#F5A623':'#22C55E'):(pct>=66?'#22C55E':pct>=33?'#F5A623':'#EF4444');
  return `<div class="gauge"><svg width="86" height="86" viewBox="0 0 86 86">
    <circle cx="43" cy="43" r="${r}" fill="none" stroke="#1a1a1a" stroke-width="7"/>
    <circle cx="43" cy="43" r="${r}" fill="none" stroke="${col}" stroke-width="7" stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 43 43)" style="filter:drop-shadow(0 0 4px ${col})"/>
    <text x="43" y="49" text-anchor="middle" fill="#f4f4f4" font-size="19" font-weight="800">${Math.round(pct)}</text></svg>
    <div class="glabel">${label}</div><div class="gsub">${sub}</div></div>`;
}
function renderRisk(d){
  const el=document.getElementById('dRisk');if(!el)return;
  const mc=d.market_ctx||{},b=mc.breadth||{},rows=d.rows||[],DET=d.detail||{},secs=d.sectors||[];
  const vix=mc.vix||16,volRisk=Math.max(0,Math.min(100,(vix-10)/25*100));
  const part=b.above50!=null?b.above50:50,N=rows.length||1;
  const overheat=Math.round(100*rows.filter(r=>{const x=DET[r.symbol]||{};return (x.ext_atr||0)>=4||(x.rsi||0)>=78}).length/N);
  const totBuy=secs.reduce((s,x)=>s+(x.n_buy||0),0)||1;
  const conc=Math.round(100*Math.max(0,...secs.map(x=>x.n_buy||0))/totBuy);
  const advPct=Math.round(100*(b.adv||0)/N);
  const regHealth=mc.spy_regime==='TREND'?85:mc.spy_regime==='CHOP'?25:55;
  el.innerHTML=gauge(volRisk,'Volatilité','VIX '+vix,true)
   +gauge(part,'Participation','% > MM50',false)
   +gauge(overheat,'Surchauffe','% surextendus',true)
   +gauge(conc,'Concentration','1er secteur',true)
   +gauge(advPct,'Ampleur hausse','% en hausse',false)
   +gauge(regHealth,'Santé régime',mc.spy_regime||'—',false);
}
function renderHero2(d){
  const el=document.getElementById('dHero2');if(!el)return;
  const rows=d.rows||[],secs=d.sectors||[],sec=(d.daily||{}).sections||{};
  const pep=rows.filter(r=>(r.grade==='S+'||r.grade==='S')&&r.verdict==='BUY').length;
  const buys=rows.filter(r=>r.verdict==='BUY').length,watch=rows.filter(r=>r.verdict==='WATCH').length;
  const guard=(sec.guardrails||[]).length,ts=secs[0];
  const card=(ic,lab,val,sub)=>`<div class="kcard"><div class="kicon">${ic}</div><div style="min-width:0"><div class="klabel">${lab}</div><div class="kval">${val}</div><div class="ksub">${sub}</div></div></div>`;
  el.innerHTML=
    card('🔥','PÉPITES DU JOUR',pep,'setups S+/S en ACHAT')
   +card('🎯','SIGNAUX ACHAT',buys,`${watch} à surveiller · ${rows.length} scannés`)
   +card('🏆','MEILLEUR SECTEUR',ts?ts.sector:'—',ts?`score ${ts.avg_score} · leader ${ts.leader.symbol}`:'—')
   +card('⚠️','GARDE-FOUS',guard,'titres surchauffés à éviter');
}
function loadPos(){try{return JSON.parse(localStorage.getItem('elio_pos')||'[]')}catch(e){return []}}
function savePos(a){localStorage.setItem('elio_pos',JSON.stringify(a))}
function addPos(){
  const inp=document.getElementById('posIn'),v=(inp.value||'').trim();if(!v)return;
  const m=v.match(/^([A-Za-z.]{1,6})[ ]+([0-9]+(?:[.,][0-9]+)?)(?:[ ]*[x*][ ]*([0-9]+))?$/);
  if(!m){inp.value='';inp.placeholder='format attendu : TICKER PRIX [xQTÉ] — ex : AAPL 195.50 x10';return}
  const a=loadPos();a.push({sym:m[1].toUpperCase(),entry:parseFloat(m[2].replace(',','.')),qty:m[3]?parseInt(m[3]):0});
  savePos(a);inp.value='';renderPositions(window.__lastD||{});
}
function delPos(i){const a=loadPos();a.splice(i,1);savePos(a);renderPositions(window.__lastD||{});}
function valu(pe,med){if(!pe||!med)return null;const r=pe/med;return r>=1.3?{l:'cher (premium)',t:'dn'}:r<=0.75?{l:'décoté',t:'up'}:{l:'dans la moyenne',t:'muted'}}
function peerRank(d,sym){const secs=(d&&d.sectors)||[];for(const s of secs){const idx=(s.members||[]).findIndex(m=>m.symbol===sym);if(idx>=0)return {rank:idx+1,n:s.members.length,sector:s.sector}}return null}
function posNarr(p,x,f,med,pr){
  const px=x.price,pl=x.plan||{},pnl=(px/p.entry-1)*100;
  const reg=x.regime==='TREND'?'tendance solide':x.regime==='CHOP'?'marché agité (range)':'momentum neutre';
  let base;
  if(pl.tp2&&px>=pl.tp2)base=`🎯 <b>TP2 atteint ($${pl.tp2})</b> — encaisse une partie, sécurise.`;
  else if(pl.tp1&&px>=pl.tp1)base=`✅ <b>TP1 franchi</b> — prochain objectif TP2 $${pl.tp2}.`;
  else if(pl.stop&&px<=pl.stop)base=`🛑 <b>STOP touché ($${pl.stop})</b> — sortie discipline.`;
  else if(pnl>=0)base=`🟢 +${pnl.toFixed(1)}% · ${reg} · RSI ${Math.round(x.rsi||0)}. Stop $${pl.stop}, vise TP1 $${pl.tp1}.`;
  else base=`🔴 ${pnl.toFixed(1)}% · ${reg} · RSI ${Math.round(x.rsi||0)}. Surveille le stop $${pl.stop}.`;
  const v=valu(f&&f.pe,med),ex=[];
  if(v)ex.push(`valorisation <b class="${v.t}">${v.l}</b> (P/E ${f.pe.toFixed(0)} vs ${med} secteur)`);
  if(pr)ex.push(`${pr.rank}ᵉ/${pr.n} de son secteur (${pr.sector})`);
  if(x.regime==='CHOP')ex.push('⚠ marché en range — cassures fragiles');
  return base+(ex.length?`<div class="muted" style="font-size:11px;margin-top:4px">${ex.join(' · ')}</div>`:'');
}
function renderPositions(d){
  const a=loadPos(),DET=(d&&d.detail)||{},F=(d&&d.fundamentals)||{},FS=F.by_sym||{},FSEC=F.by_sector||{},el=document.getElementById('posBody');if(!el)return;
  if(!a.length){el.innerHTML='<span class="muted" style="font-size:12px">Tape un titre + ton prix d\'entrée pour un suivi live narré — ex : <b>AAPL 195.50 x10</b>. Stocké sur ton navigateur.</span>';return}
  el.innerHTML='<div style="overflow:auto"><table><thead><tr><th class="l">Titre</th><th>Entrée</th><th>Prix</th><th>P&L %</th><th>P&L $</th><th>P/E · SECT.</th><th>SCORE</th><th class="l">Ce qui se passe (live)</th><th></th></tr></thead><tbody>'+a.map((p,i)=>{
    const x=DET[p.sym]||{},px=x.price,f=FS[p.sym]||{},med=(FSEC[f.sector]||{}).median_pe,pr=peerRank(d,p.sym);
    if(px==null)return `<tr><td class="l sym">${p.sym}</td><td>$${p.entry}</td><td colspan="6" class="muted">hors univers scanné (45 leaders) — prix non suivi</td><td><span onclick="delPos(${i})" style="cursor:pointer;color:#8794ab">✕</span></td></tr>`;
    const pnl=(px/p.entry-1)*100, pnld=p.qty?(px-p.entry)*p.qty:null, v=valu(f.pe,med);
    return `<tr><td class="l sym">${p.sym}</td><td>$${p.entry}</td><td>$${px}</td>
      <td class="${pnl>=0?'up':'dn'}" style="font-weight:800">${pnl>=0?'+':''}${pnl.toFixed(1)}%</td>
      <td class="${pnl>=0?'up':'dn'}">${pnld!=null?(pnld>=0?'+':'')+'$'+Math.round(pnld).toLocaleString('fr-FR'):'—'}</td>
      <td>${f.pe?f.pe.toFixed(0):'—'} <span class="muted">/ ${med||'—'}</span>${v?` <span class="${v.t}" style="font-size:10px">${v.l.split(' ')[0]}</span>`:''}</td>
      <td><span class="sc" style="color:${clr(x.score||0)}">${x.score!=null?x.score:'—'}</span></td>
      <td class="l" style="font-size:12px;line-height:1.5">${posNarr(p,x,f,med,pr)}</td>
      <td><span onclick="delPos(${i})" style="cursor:pointer;color:#8794ab">✕</span></td></tr>`;
  }).join('')+'</tbody></table></div>';
}
window.addPos=addPos;window.delPos=delPos;
function renderToday(cal,news){
  const cat=document.getElementById('dCatalysts');
  if(cat){const it=(cal.items||[]).slice(0,7);
    cat.innerHTML=it.length?it.map(x=>{const soon=x.dte!=null&&x.dte<7,dd=x.date?x.date.slice(8,10)+'/'+x.date.slice(5,7):'';return `<div onclick="go('${x.sym}')" style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #1a1a1a;cursor:pointer;font-size:12.5px"><span><b>${x.sym}</b> <span class="muted">${dd}</span></span><span class="${soon?'dn':'muted'}" style="${soon?'font-weight:800':''}">${soon?'🔴 ':''}${x.dte<=0?'auj.':'J-'+x.dte}</span></div>`}).join(''):'<span class="muted">aucun résultat imminent</span>';}
  const nw=document.getElementById('dNews2');
  if(nw){const it=(news.items||[]).slice(0,6);
    nw.innerHTML=it.length?it.map(n=>`<div onclick="${n.link?`window.open('${n.link}','_blank')`:''}" style="padding:8px 0;border-bottom:1px solid #1a1a1a;cursor:pointer;font-size:12px;line-height:1.45"><span style="color:#FFD27A;font-weight:700">${n.sym}</span> ${n.fr||n.title} <span class="muted" style="font-size:10px">${n.time||''}</span></div>`).join(''):'<span class="muted">collecte des news en cours…</span>';}
}
function renderWeekly(w){
  const el=document.getElementById('dWeekly');if(!el)return;
  const picks=((w&&w.data)||{}).picks||[];
  if(!picks.length){el.innerHTML='<span class="muted" style="font-size:12px">aucun setup ne passe les filtres cette semaine — marché en range, mieux vaut attendre.</span>';return}
  el.innerHTML=picks.map(p=>{const lv=p.levels||{},op=p.option||{},od=op.exp?op.exp.slice(8,10)+'/'+op.exp.slice(5,7):'';return `<div class="seccard" onclick="go('${p.symbol}')">
    <div class="sh" style="color:#F5A623">${p.icon||'⭐'} ${p.symbol}<span class="grade ${gcls(p.grade)}" style="margin-left:auto">${p.grade}</span></div>
    <div class="muted" style="font-size:10px;margin:5px 0">${p.sector||''} · score ${p.score} · confiance ${p.confidence}</div>
    <div style="font-size:11px;line-height:1.45;margin-bottom:7px">${(p.why||'').slice(0,100)}</div>
    <div style="display:flex;gap:9px;font-size:11px;flex-wrap:wrap"><span>entrée <b>$${lv.entry}</b></span><span class="dn">stop $${lv.stop}</span><span class="up">TP2 $${lv.tp2}</span></div>
    ${op&&op.strike?`<div style="margin-top:7px;font-size:11px;color:#FFD27A">💎 ${(op.bucket||'').toUpperCase()} $${op.strike}${od?' · '+od:''}${op.pop!=null?' · POP '+op.pop+'%':''}</div>`:''}
  </div>`}).join('');
}
function recCol(t){return t==='strong'?'#22C55E':t==='buy'?'#4ade80':t==='watch'?'#F5A623':t==='wait'?'#7FB3FF':'#EF4444'}
function renderRecs(d){
  const el=document.getElementById('dRecs');if(!el)return;
  const recs=(d.recommendations||[]).filter(r=>r.tone==='strong'||r.tone==='buy'||(r.tone==='watch'&&r.conviction>=48)).slice(0,8);
  if(!recs.length){el.innerHTML='<span class="muted" style="font-size:12px;padding:4px">Aucun achat franc aujourd hui — le marché ne tend pas, mieux vaut attendre un meilleur contexte.</span>';return}
  el.innerHTML=recs.map(r=>{const c=recCol(r.tone);return `<div class="seccard" onclick="go('${r.symbol}')" style="border-color:${c}33">
    <div class="sh" style="color:${c}">${r.symbol}<span style="margin-left:auto;font-size:10.5px;font-weight:800;letter-spacing:.5px">${r.decision}</span></div>
    <div style="display:flex;align-items:center;gap:8px;margin:8px 0 6px"><div style="flex:1;height:6px;background:#1a1a1a;border-radius:4px;overflow:hidden"><div style="height:100%;width:${r.conviction}%;background:${c};box-shadow:0 0 7px ${c}"></div></div><b style="color:${c};font-size:13px">${r.conviction}</b></div>
    <div class="muted" style="font-size:10.5px">${r.sector||''} · ${r.grade} · <span class="${r.change>=0?'up':'dn'}">${r.change>=0?'+':''}${r.change}%</span></div>
    <div style="font-size:11px;margin-top:6px;line-height:1.45">${r.pros[0]||''}</div></div>`}).join('');
}
function renderMovers(d){
  const rows=(d.rows||[]).filter(r=>typeof r.change==='number');
  const g=document.getElementById('dGainers'),l=document.getElementById('dLosers');
  const ln=r=>`<div onclick="go('${r.symbol}')" style="display:flex;justify-content:space-between;align-items:center;padding:7px 2px;border-bottom:1px solid #1a1a1a;cursor:pointer;font-size:12.5px"><span><b style="color:#FFD27A">${r.symbol}</b> <span class="muted" style="font-size:11px">$${r.price}</span></span><span class="${r.change>=0?'up':'dn'}" style="font-weight:800">${r.change>=0?'+':''}${r.change}%</span></div>`;
  if(g){const up=[...rows].sort((a,b)=>b.change-a.change).slice(0,7);g.innerHTML=up.map(ln).join('')||'<span class="muted">—</span>';}
  if(l){const dn=[...rows].sort((a,b)=>a.change-b.change).slice(0,7);l.innerHTML=dn.map(ln).join('')||'<span class="muted">—</span>';}
}
function eud3(s){return s?s.slice(8,10)+'/'+s.slice(5,7)+'/'+s.slice(0,4):s}
async function loadDetail(sym){
  const el=document.getElementById('dDetail');if(!el)return;
  el.style.display='block';el.innerHTML='<div class="scard" style="padding:20px"><span class="muted">chargement '+sym+'…</span></div>';
  el.scrollIntoView({behavior:'smooth',block:'start'});
  try{
    const o=await(await fetch('/options/'+sym)).json();
    const d=((window.__lastD||{}).detail||{})[sym]||{};
    const dec=o.decision,dc=dec?(dec.tone==='strong'?'#22C55E':dec.tone==='buy'?'#4ade80':dec.tone==='watch'?'#F5A623':dec.tone==='wait'?'#FFB23F':'#EF4444'):'#888';
    const lv=d.plan||{},bp=o.best_pick;
    const decCard=dec?`<div style="background:linear-gradient(135deg,${dc}14,#0d0d0d);border:1px solid ${dc}55;border-radius:14px;padding:16px 18px;margin-bottom:14px"><div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap"><div style="font-size:23px;font-weight:800;color:${dc};text-shadow:0 0 14px ${dc}55">${dec.decision}</div><div style="flex:1;min-width:130px"><div class="muted" style="font-size:10px;letter-spacing:1px">CONVICTION</div><div style="height:8px;background:#1a1a1a;border-radius:5px;margin-top:5px;overflow:hidden"><div style="height:100%;width:${dec.conviction}%;background:${dc};box-shadow:0 0 8px ${dc}"></div></div></div><div style="font-size:21px;font-weight:800;color:${dc}">${dec.conviction}<span style="font-size:12px;color:#888">/100</span></div></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:14px"><div><div style="font-size:10px;letter-spacing:1px;color:#22C55E;font-weight:700;margin-bottom:6px">✓ FORCES</div>${(dec.pros||[]).map(p=>`<div style="font-size:12px;margin:4px 0"><span class="up">✓</span> ${p}</div>`).join('')}</div><div><div style="font-size:10px;letter-spacing:1px;color:#EF4444;font-weight:700;margin-bottom:6px">⚠ RISQUES</div>${(dec.cons||[]).map(c=>`<div style="font-size:12px;margin:4px 0"><span class="dn">✗</span> ${c}</div>`).join('')||'<div class="muted" style="font-size:12px">aucun risque majeur</div>'}</div></div><div style="margin-top:13px;padding-top:12px;border-top:1px solid #ffffff10;font-size:13px"><b style="color:${dc}">→ Action :</b> ${dec.action}</div></div>`:'';
    const k=(l,v)=>`<div class="kpi" style="margin:0;padding:11px 13px"><div style="font-size:9.5px;letter-spacing:.5px;text-transform:uppercase;color:#8a8a8a">${l}</div><div style="font-size:16px;font-weight:800;margin-top:3px">${v}</div></div>`;
    const kpis=`<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px;margin-bottom:14px">${k('SCORE',`<span style="color:${clr(d.score||0)}">${d.score!=null?d.score:'—'} ${d.grade||''}</span>`)}${k('RÉGIME',d.regime==='TREND'?'TENDANCE':d.regime==='CHOP'?'RANGE':'NEUTRE')}${k('RSI · RS',`${Math.round(d.rsi||0)} · ${Math.round(d.rs||0)}`)}${k('QUALITÉ SETUP',(d.setup_quality!=null?d.setup_quality:'—')+'/100')}${k('P/E · SECT.',(o.pe?o.pe.toFixed(0):'—')+' / '+(o.sector_median_pe||'—'))}${k('PLAN',`$${lv.entry} <span class="dn">$${lv.stop}</span> <span class="up">$${lv.tp2}</span>`)}</div>`;
    const opt=bp?`<div style="background:rgba(255,210,122,.06);border:1px solid #FFD27A44;border-radius:10px;padding:13px 15px"><div style="font-size:10px;color:#FFD27A;font-weight:700;letter-spacing:1px;margin-bottom:5px">💎 OPTION RECOMMANDÉE</div><div style="font-size:14px;font-weight:700">${(bp.bucket||'').toUpperCase()} · ${eud3(bp.exp)} · strike $${bp.strike} <span style="color:${clr(bp.suit)}">${bp.grade}</span></div><div class="muted" style="font-size:12px;margin-top:5px">coût $${(bp.cost||0).toLocaleString('fr-FR')} · POP ${bp.pop}% · danger ${bp.danger} · si $${bp.tgt} → <span class="${bp.pot>=0?'up':'dn'}">${bp.pot>=0?'+':''}${bp.pot}%</span></div></div>`:'';
    el.innerHTML=`<div class="scard" style="border-color:${dc}44"><div class="shead" style="color:${dc}"><span class="ico">📈</span> ${sym} <span class="muted" style="font-weight:400;font-size:12px">${o.name||''}</span><span style="margin-left:auto;cursor:pointer;color:#888" onclick="document.getElementById('dDetail').style.display='none'">✕ fermer</span></div><div style="padding:16px">${decCard}${kpis}${opt}<div class="muted" style="font-size:11.5px;margin-top:12px;line-height:1.5">🔬 ${o.chart_read||''}</div></div></div>`;
  }catch(e){el.innerHTML='<div class="scard" style="padding:20px"><span class="dn">erreur de chargement de '+sym+'</span></div>';}
}
window.go=loadDetail;window.loadDetail=loadDetail;
async function dailyTick(){try{
  const [d,cal,news,weekly]=await Promise.all([
    fetch('/scan').then(r=>r.json()),
    fetch('/cal-feed').then(r=>r.json()).catch(()=>({})),
    fetch('/news-feed').then(r=>r.json()).catch(()=>({})),
    fetch('/weekly-feed').then(r=>r.json()).catch(()=>({}))]);
  renderDaily(d);renderToday(cal,news);renderWeekly(weekly);
  const t=q('dTick');if(t){t.classList.remove('flash');void t.offsetWidth;t.classList.add('flash');}
}catch(e){}}
setInterval(dailyTick,15000);dailyTick();
