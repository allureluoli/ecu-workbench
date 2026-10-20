'use strict';
const $=s=>document.querySelector(s);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=x=>Number(x||0).toLocaleString('zh-CN');
const state={version:null,offset:0,total:0,versionRequest:0,faultOffset:0,faultTotal:0,sid:null,diffSid:null};
const labels={verified:'校验通过',corrected:'已修正并复验',invalid:'校验不匹配',unsupported:'未支持',ambiguous:'规则冲突',profile_error:'规则错误'};
function error(e){$('#error').hidden=false;$('#error').textContent=e.message||String(e);}
function toast(text){$('#toast').hidden=false;$('#toast').textContent=text;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,3500);}
function run(fn){return (...args)=>Promise.resolve().then(()=>fn(...args)).catch(error);}
async function api(path,options={}){
  const r=await fetch(path,options);const data=await r.json();
  if(!r.ok)throw new Error(data.error||`HTTP ${r.status}`);
  $('#error').hidden=true;return data;
}
function post(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body});}
function postJSON(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
async function download(path,name){
  const r=await fetch(path);if(!r.ok)throw new Error((await r.json()).error||'下载失败');
  const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download=name;a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
document.querySelectorAll('nav button').forEach(b=>b.onclick=run(async()=>{
  document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('active',x===b));
  document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===b.dataset.tab));
  if(b.dataset.tab==='modules')await modules();
  if(b.dataset.tab==='files')await profiles();
}));
async function status(){
  const d=await api('/api/status');
  $('#dataset').textContent=d.dataset+(d.demo?' · 合成演示数据':' · 本地导入数据')+' · 无登录';
  const names={file_vers:'版本',modules:'模块',fault_codes:'故障码',module_configs:'功能目录',decoded_packs:'已解析地图包',assets:'原始附件'};
  $('#stats').innerHTML=Object.entries(names).map(([k,v])=>`<div class="stat">${v}<b>${fmt(d.counts[k])}</b></div>`).join('');
}
async function versions(){
  const request=++state.versionRequest;
  $('#versionList').innerHTML='<p>加载中…</p>';
  $('#versionCount').textContent='查询中…';
  const q=new URLSearchParams({q:$('#versionQuery').value,limit:100,offset:state.offset});
  if($('#directory').value)q.set('dir',$('#directory').value);
  const d=await api('/api/versions?'+q);if(request!==state.versionRequest)return;state.total=d.total;
  $('#versionCount').textContent=`${fmt(d.total)} 个版本 · 第 ${Math.floor(state.offset/100)+1} 页`;
  $('#versionList').innerHTML=d.items.map(v=>`<button class="version ${state.version===v.id?'selected':''}" data-id="${v.id}"><b>${esc(v.name)}</b><small>${esc(v.resolve_type)} · ${v.module_count} 个模块 · ID ${v.id}</small></button>`).join('')||'<p>没有匹配版本。</p>';
  $('#versionPrev').disabled=state.offset===0;$('#versionNext').disabled=state.offset+100>=d.total;
}
$('#versionSearch').onclick=run(()=>{state.offset=0;return versions();});
$('#versionQuery').onkeydown=e=>{if(e.key==='Enter')$('#versionSearch').click();};
$('#directory').onchange=run(()=>{state.offset=0;return versions();});
$('#versionPrev').onclick=run(()=>{state.offset=Math.max(0,state.offset-100);return versions();});
$('#versionNext').onclick=run(()=>{state.offset+=100;return versions();});
$('#versionList').onclick=run(async e=>{
  const b=e.target.closest('[data-id]');if(!b)return;
  state.version=Number(b.dataset.id);const d=await api('/api/version?id='+state.version);
  document.querySelectorAll('.version').forEach(x=>x.classList.toggle('selected',Number(x.dataset.id)===state.version));
  $('#maps').hidden=true;$('#mapPreview').hidden=true;
  $('#versionDetail').innerHTML=`<h2>${esc(d.name)}</h2><p>版本 ${d.id} · ${esc(d.resolve_type)} · ${d.modules.length} 个模块</p>
    <p class="mono">识别特征：${esc(d.feature_code||'未提供')}</p><p>附件和模块名仅作为数据目录展示，不执行未验证的一键标定。</p>`+
    d.modules.map(m=>`<div class="module"><b>${esc(m.name)}</b> <span class="tag">${esc(m.module_type)}</span><p>${esc(m.file_name||'无独立附件')}</p>
    ${m.decoded?`<button data-maps="${m.id}">预览地图</button>`:'<small>没有已解析地图 JSON</small>'}
    ${m.asset_available?`<a href="/api/asset?module=${m.id}">下载原始附件</a>`:''}</div>`).join('');
});
$('#versionDetail').onclick=run(async e=>{
  const b=e.target.closest('[data-maps]');if(!b)return;
  const mid=Number(b.dataset.maps);const q=new URLSearchParams({ver:state.version,module:mid});
  const d=await api('/api/maps?'+q);$('#maps').hidden=false;$('#mapPreview').hidden=true;
  $('#maps').innerHTML=`<h2>地图快照 · ${d.items.length} 张</h2><p>${esc(d.message)}</p><a href="/api/maps?${q}&download=1">导出地图 JSON</a>`+
    d.items.map((m,i)=>`<button class="map-button" data-module="${mid}" data-map="${i}">${esc(m.name)} <small>${m.col} × ${m.row} · ${esc(m.zBit)} · KP地址 ${esc(m.zAddress)}</small></button>`).join('');
});
$('#maps').onclick=run(async e=>{
  const b=e.target.closest('[data-map]');if(!b)return;
  const d=await api('/api/map?'+new URLSearchParams({ver:state.version,module:b.dataset.module,index:b.dataset.map}));
  $('#mapPreview').hidden=false;
  $('#mapPreview').innerHTML=`<h2>${esc(d.name)}</h2><span class="tag">${esc(d.source)}</span>`+
    d.warnings.map(x=>`<p class="warn">${esc(x)}</p>`).join('')+
    `<pre>${esc(JSON.stringify(d.metadata,null,2))}</pre>`+
    (d.values.length?'<div class="scroll"><table><tbody>'+d.values.map(row=>'<tr>'+row.map(v=>`<td>${esc(v??'未知')}</td>`).join('')+'</tr>').join('')+'</tbody></table></div>':'');
});
async function modules(){
  const d=await api('/api/modules');
  $('#moduleRows').innerHTML=d.items.map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.module_type)}</td><td>${fmt(m.module_count)}</td><td>${fmt(m.version_count)}</td></tr>`).join('');
}
function faultParams(){
  const q=new URLSearchParams({q:$('#faultQuery').value,code:$('#faultCode').value});
  if($('#faultVersion').value)q.set('ver',$('#faultVersion').value);return q;
}
async function faults(){
  const q=faultParams();q.set('offset',state.faultOffset);
  const d=await api('/api/faults?'+q);state.faultTotal=d.total;
  $('#faultCount').textContent=`${fmt(d.total)} 条 · 第 ${Math.floor(state.faultOffset/100)+1} 页`;
  $('#faultRows').innerHTML=d.items.map(r=>`<tr><td>${esc(r.version_name)}</td><td class="mono">${esc(r.code)}</td><td>${esc(r.desc)}</td></tr>`).join('')||'<tr><td colspan="3">没有匹配记录。</td></tr>';
  $('#faultPrev').disabled=state.faultOffset===0;$('#faultNext').disabled=state.faultOffset+100>=d.total;
}
$('#faultSearch').onclick=run(()=>{state.faultOffset=0;return faults();});
$('#faultPrev').onclick=run(()=>{state.faultOffset=Math.max(0,state.faultOffset-100);return faults();});
$('#faultNext').onclick=run(()=>{state.faultOffset+=100;return faults();});
$('#faultExport').onclick=run(()=>download('/api/faults.csv?'+faultParams(),'fault-codes.csv'));
function renderChecksum(d,pf=null){
  $('#correct').disabled=!d.can_correct;$('#binDownload').disabled=!state.sid;$('#reportDownload').disabled=!state.sid;
  $('#fileResult').innerHTML=`<h2>${esc(labels[d.checksum]||d.checksum)}</h2><p>${esc(d.message)}</p>`+
    `<div class="mono">当前 SHA-256：${esc(d.sha256)}</div>`+
    (d.baseline_sha256?`<div class="mono">基准 SHA-256：${esc(d.baseline_sha256)}</div>`:'')+
    (d.profile_name?`<p>规则：${esc(d.profile_name)}<br>依据：${esc(d.evidence)}</p>`:'')+
    (pf?`<p>长度：${fmt(pf.size)} B · ${esc(pf.address_mapping)}</p>`:'')+
    `<p>${esc(d.flashable)}</p>`+
    ((d.blocks||[]).length?'<table><thead><tr><th>校验块</th><th>算法</th><th>当前值</th><th>计算值</th><th>结果</th></tr></thead><tbody>'+d.blocks.map(b=>`<tr><td>${esc(b.id)}</td><td>${esc(b.algorithm)}</td><td>${esc(b.stored)}</td><td>${esc(b.expected)}</td><td>${b.valid?'通过':'不匹配'}</td></tr>`).join('')+'</tbody></table>':'')+
    ((d.corrections||[]).length?`<pre>${esc(JSON.stringify(d.corrections,null,2))}</pre>`:'');
}
async function refreshFile(){if(!state.sid)return;const d=await api('/api/report?sid='+state.sid);renderChecksum(d.checksum,d.preflight);}
$('#binFile').onchange=run(async e=>{
  const file=e.target.files[0];if(!file)return;
  if(file.size>64*1024*1024)throw new Error('单文件上限 64 MiB');
  const d=await post('/api/upload',await file.arrayBuffer());state.sid=d.sid;renderChecksum(d.checksum,d.preflight);
});
$('#baselineFile').onchange=run(async e=>{
  const file=e.target.files[0];if(!file)return;if(!state.sid)throw new Error('先加载当前 BIN');
  if(file.size>64*1024*1024)throw new Error('单文件上限 64 MiB');
  renderChecksum(await post('/api/baseline?sid='+state.sid,await file.arrayBuffer()));e.target.value='';
});
$('#verify').onclick=run(refreshFile);
$('#correct').onclick=run(async()=>{renderChecksum(await post('/api/correct?sid='+state.sid,new Uint8Array()));toast('已修正并完成复验');});
$('#binDownload').onclick=run(async()=>{await download('/api/bin?sid='+state.sid,'checked.bin');await refreshFile();});
$('#reportDownload').onclick=run(()=>download('/api/report?sid='+state.sid+'&download=1','inspection-report.json'));
$('#diffFile').onchange=run(async e=>{
  const file=e.target.files[0];if(!file)return;if(file.size>64*1024*1024)throw new Error('单文件上限 64 MiB');
  state.diffSid=(await post('/api/upload',await file.arrayBuffer())).sid;$('#compare').disabled=false;
});
$('#compare').onclick=run(async()=>{
  if(!state.sid||!state.diffSid)throw new Error('先加载当前 BIN 和对比文件');
  $('#diffResult').textContent=JSON.stringify(await postJSON('/api/diff',{a:state.sid,b:state.diffSid}),null,2);
});
async function profiles(){
  const d=await api('/api/profiles');$('#profileCount').textContent=d.profiles.length+' 条';
  $('#profiles').innerHTML=d.profiles.map(p=>`<p><b>${esc(p.name)}</b> · 版本 ${p.version}<br>${esc(p.evidence)}</p>`).join('')||'<p class="warn">未配置真实 ECU 专属规则，当前文件可能显示未支持。</p>';
}
$('#profileFile').onchange=run(async e=>{
  const f=e.target.files[0];if(!f)return;if(f.size>1024*1024)throw new Error('规则文件上限 1 MiB');
  const d=await post('/api/profiles',await f.text());toast('已导入 '+d.count+' 条规则');await profiles();await refreshFile();await status();e.target.value='';
});
run(async()=>{
  await status();const dirs=(await api('/api/directories')).items;
  const byId=new Map(dirs.map(d=>[d.id,d]));
  function path(d){const names=[],seen=new Set();while(d&&!seen.has(d.id)){seen.add(d.id);names.unshift(d.name);d=byId.get(d.parent_id);}return names.join(' / ');}
  $('#directory').innerHTML='<option value="">所有目录</option>'+dirs.map(d=>`<option value="${d.id}">${esc(path(d))}</option>`).join('');
  const all=await api('/api/versions?limit=1000');
  $('#faultVersion').innerHTML='<option value="">全部版本</option>'+all.items.map(v=>`<option value="${v.id}">${esc(v.name)}</option>`).join('');
  await versions();await faults();await profiles();
})();
