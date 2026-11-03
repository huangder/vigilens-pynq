/* Local scalar measurement history. No media, identities, or network writes. */
(function(root){
  'use strict';
  function create(options){
    var rows=[],lastId=null,lastTs=null,lastSaved=null,session=String(options.now()),persistent=true;
    function trim(now){rows=rows.filter(function(r){return r.at<=now&&now-r.at<=options.retentionMs;}).slice(-options.maxRecords);}
    function valid(r){return r&&Number.isFinite(r.at)&&Number.isFinite(r.sourceTs)&&Number.isFinite(r.frameId)&&
      Number.isFinite(r.bpm)&&r.bpm>=options.minBpm&&Number.isFinite(r.conf)&&r.conf>=0&&r.conf<=1&&
      typeof r.session==='string'&&typeof r.source==='string';}
    try{
      var saved=JSON.parse(options.storage.getItem(options.key)||'null');
      if(saved&&saved.version===1&&Array.isArray(saved.records))rows=saved.records.filter(valid);
    }catch(error){persistent=false;}
    trim(options.now());
    if(rows.length){var latest=rows[rows.length-1];lastId=latest.frameId;lastTs=latest.sourceTs;lastSaved=latest.at;session=latest.session;}
    function save(){try{options.storage.setItem(options.key,JSON.stringify({version:1,records:rows}));}catch(error){persistent=false;}}
    function accept(frame,now){
      trim(now);
      if(lastSaved!==null&&now<lastSaved){lastSaved=null;session=String(now);}
      if(!frame||!frame.vital||!frame.face||!frame.quality||!Number.isFinite(frame.frame_id)||!Number.isFinite(frame.ts))return false;
      // Disconnect placeholders can have synthetic IDs/timestamps. They must
      // not replace the real stream watermark or split its stored session.
      if(frame.status==='disconnected')return false;
      if(lastId!==null&&(frame.frame_id<lastId||frame.ts<lastTs)){session=String(now);lastSaved=null;}
      if(lastId===frame.frame_id&&lastTs===frame.ts)return false;
      lastId=frame.frame_id;lastTs=frame.ts;
      var bpm=frame.vital.hr_bpm,conf=frame.vital.hr_conf;
      if(frame.face.visible<=0||['unreliable','adjust_posture','disconnected','done'].indexOf(frame.status)>=0||
         !(frame.quality.overall>=options.minimumQuality)||!Number.isFinite(bpm)||bpm<options.minBpm||
         !Number.isFinite(conf)||conf<0||conf>1)return false;
      if(lastSaved!==null&&now-lastSaved<options.intervalMs)return false;
      rows.push({at:now,sourceTs:frame.ts,frameId:frame.frame_id,bpm:bpm,conf:conf,session:session,source:options.source});
      trim(now);lastSaved=now;save();return true;
    }
    function list(from,to){trim(options.now());return rows.filter(function(r){return (from===null||r.at>=from)&&(to===null||r.at<=to);}).map(function(r){return Object.assign({},r);});}
    function csv(from,to){
      function field(value){var s=String(value);if(/^[=+@-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}
      return [['observed_time','heart_rate_bpm','confidence','source_seconds','frame_id','source','session']].concat(
        list(from,to).map(function(r){return [new Date(r.at).toISOString(),r.bpm,r.conf,r.sourceTs,r.frameId,r.source,r.session];})
      ).map(function(r){return r.map(field).join(',');}).join('\r\n')+'\r\n';
    }
    return {accept:accept,list:list,csv:csv,persistent:function(){return persistent;}};
  }
  function mount(document,store,config){
    var panel=document.getElementById('hrHistoryPanel');if(!panel)return {refresh:function(){}};
    var from=document.getElementById('hrHistoryFrom'),to=document.getElementById('hrHistoryTo');
    var body=document.getElementById('hrHistoryRows'),count=document.getElementById('hrHistoryCount');
    function bounds(){return [from.value?new Date(from.value).getTime():null,to.value?new Date(to.value).getTime():null];}
    function refresh(){
      var range=bounds(),records=store.list(range[0],range[1]);
      count.textContent='筛选到 '+records.length+' 条有效观测；列表显示最近 '+config.listLimit+' 条。'+
        (store.persistent()?'保存在当前浏览器。':'浏览器存储不可用，当前仅保留内存，请及时导出。');
      body.textContent='';
      records.slice(-config.listLimit).reverse().forEach(function(r){
        var tr=document.createElement('tr');
        [new Date(r.at).toLocaleString(),r.bpm.toFixed(1),r.conf.toFixed(3),r.source+' / '+r.session].forEach(function(value){
          var td=document.createElement('td');td.textContent=value;tr.appendChild(td);
        });body.appendChild(tr);
      });
    }
    from.addEventListener('change',refresh);to.addEventListener('change',refresh);
    panel.addEventListener('toggle',refresh);
    document.getElementById('hrHistoryExport').addEventListener('click',function(){
      var range=bounds(),url=URL.createObjectURL(new Blob(['\ufeff'+store.csv(range[0],range[1])],{type:'text/csv;charset=utf-8'}));
      var a=document.createElement('a');a.href=url;a.download='vigilens-heart-rate-history.csv';a.click();
      setTimeout(function(){URL.revokeObjectURL(url);},0);
    });
    refresh();return {refresh:function(){if(panel.open)refresh();}};
  }
  var api={create:create,mount:mount};root.VigiLensHrHistory=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
