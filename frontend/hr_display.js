/* Presentation cache only. Cached values never become measurements/alerts. */
(function(root){
  "use strict";
  function create(options){
    var freshMs=options&&typeof options.freshMs==="number"&&options.freshMs>0?options.freshMs:0;
    var last=null,frame=null,rx=0,lastId=null,lastTs=null;
    function reset(){last=null;frame=null;rx=0;lastId=null;lastTs=null;}
    function accept(next,now,eligible){
      if(!next||!next.vital||!next.face)return false;
      // Transport placeholders may use synthetic frame IDs. Keep the real
      // stream watermark so a disconnect cannot erase the historical value.
      if(next.status==="disconnected"){frame=next;return false;}
      if(lastId!==null&&(next.frame_id<lastId||next.ts<lastTs))reset();
      if(lastId===next.frame_id&&lastTs===next.ts)return false;
      lastId=next.frame_id;lastTs=next.ts;frame=next;rx=now;
      var hr=next.vital.hr_bpm;
      var valid=eligible!==false&&next.face.visible>0&&
        ["unreliable","disconnected","adjust_posture","done"].indexOf(next.status)<0&&
        typeof hr==="number"&&isFinite(hr)&&hr>0;
      if(valid)last={value:hr,confidence:next.vital.hr_conf,at:now,frameId:next.frame_id};
      return valid;
    }
    function view(now,connected){
      var age=last?Math.max(0,(now-last.at)/1000):null;
      var offline=connected===false||!frame||frame.status==="disconnected";
      var live=last&&!offline&&now-rx<=freshMs&&last.frameId===frame.frame_id;
      if(!last)return {text:"正在测量",unit:"",kind:"waiting",label:offline?"等待连接":"正在测量",note:"取得有效心率后自动显示",age:null};
      var ageText=age<1?"刚刚":Math.floor(age)+"秒前";
      return {text:last.value.toFixed(1),unit:"bpm",kind:live?"current":"history",
        label:live?"当前估计":"最近有效值 · 历史",
        note:live?"有效估计 · "+ageText:(offline?"连接中断":"当前信号不足，更新中")+" · "+ageText+" · 非当前测量",
        age:age,value:last.value};
    }
    return {accept:accept,view:view,reset:reset};
  }
  var api={create:create};root.VigiLensHrDisplay=api;
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
})(typeof globalThis!=="undefined"?globalThis:this);
