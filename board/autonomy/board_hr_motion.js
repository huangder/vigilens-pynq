/* Display animation only. Targets and contract frames are never overwritten. */
(function(root){
  'use strict';
  function create(options){
    var lastId=null,lastTs=null,rx=0,valid=false,lastValid=null;
    var from=null,target=null,valueAt=0,clockFrom=null,clockTarget=null,clockAt=0;
    function fraction(now,start,duration){return Math.max(0,Math.min(1,(now-start)/duration));}
    function number(now){
      if(target===null)return null;
      var t=fraction(now,valueAt,options.transitionMs),ease=t*t*(3-2*t);
      return from+(target-from)*ease;
    }
    function time(now){
      if(clockTarget===null)return null;
      return clockFrom+(clockTarget-clockFrom)*fraction(now,clockAt,options.viewportMs);
    }
    function reset(){lastId=lastTs=lastValid=from=target=clockFrom=clockTarget=null;valid=false;rx=0;}
    function accept(frame,now,value){
      if(frame.status==='disconnected'){
        valid=false;lastValid=null;from=target=null;clockFrom=clockTarget=time(now);return false;
      }
      if(lastId!==null&&(frame.frame_id<lastId||frame.ts<lastTs))reset();
      if(lastId===frame.frame_id&&lastTs===frame.ts)return false;
      var previousTime=time(now);
      clockFrom=previousTime===null?frame.ts:previousTime;
      clockTarget=Math.max(clockFrom,frame.ts);clockAt=now;
      lastId=frame.frame_id;lastTs=frame.ts;rx=now;
      valid=typeof value==='number'&&isFinite(value)&&value>0;
      if(valid){
        if(target!==value){
          from=lastValid!==null&&now-lastValid<=options.freshMs?number(now):value;
          target=value;valueAt=now;
        }
        lastValid=now;
      }
      return valid;
    }
    function view(now){
      var current=valid&&now-rx<=options.freshMs;
      return {current:current,value:current?number(now):null,target:current?target:null,
        historicalValue:!current&&target!==null?number(now):null,historicalTarget:!current?target:null,
        historyAnimating:!!(!current&&target!==null&&from!==target&&now-valueAt<options.transitionMs&&
          lastValid!==null&&now-lastValid<=options.freshMs),
        animating:!!(current&&from!==target&&now-valueAt<options.transitionMs),
        scrolling:clockTarget!==null&&clockFrom!==clockTarget&&now-clockAt<options.viewportMs};
    }
    return {accept:accept,view:view,time:time,reset:reset};
  }
  var api={create:create};root.VigiLensBoardHrMotion=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
