/* Presentation annotations only: never insert or overwrite measurement frames. */
(function(root){
  'use strict';
  function annotations(history,getter,options){
    var bridges=[],tail=[],last=null,lastTs=null,lastId=null,gap=false,end=null;
    history.forEach(function(frame){
      if(lastTs!==null&&(frame.ts<lastTs||frame.frame_id<lastId)){
        bridges=[];last=null;gap=false;
      }
      lastTs=frame.ts;lastId=frame.frame_id;end=frame;
      if(frame.status==='disconnected'){last=null;gap=false;return;}
      var value=getter(frame);
      if(typeof value!=='number'||!isFinite(value)){gap=true;return;}
      var point={ts:frame.ts,value:value};
      if(last&&point.ts>last.ts&&(gap||point.ts-last.ts>options.sampleGapSeconds)&&
          point.ts-last.ts<=options.bridgeSeconds)bridges.push([last,point]);
      last=point;gap=false;
    });
    var age=last&&end?end.ts-last.ts:null;
    if(last&&end&&end.status!=='disconnected'&&age>0&&age<=options.holdSeconds)
      tail=[last,{ts:end.ts,value:last.value}];
    return {bridges:bridges,tail:tail,age:age};
  }
  var api={annotations:annotations};root.VigiLensBoardHrTrend=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
