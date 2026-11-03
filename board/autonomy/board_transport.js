/* Real board HTTP transport. No mock generation or measurement rewriting. */
(function (root) {
  "use strict";
  function create(options) {
    var active=false,generation=0,timer=null,controller=null,lastId=null,lastTs=null,lastProgress=null,lastState=null;
    var timed=typeof options.now==='function';
    function report(kind,message){if(kind!==lastState){lastState=kind;options.onState(kind,message);}}
    function failed(message){
      var retry=timed&&lastProgress!==null&&options.now()-lastProgress<options.freshMs;
      report(retry?'retrying':'down',message);
    }
    function stop() {
      active=false;generation++;
      if(timer!==null)options.clearTimeout(timer);
      timer=null;
      if(controller)controller.abort();
      controller=null;
    }
    async function poll(token) {
      if(!active||token!==generation)return;
      var abort=new options.AbortController();controller=abort;
      var deadline=options.setTimeout(function(){abort.abort();},options.freshMs);
      try {
        var response=await options.fetch('/api/latest',{cache:'no-store',signal:abort.signal});
        if(!response.ok)throw Error('HTTP '+response.status);
        var frame=await response.json();
        var errors=options.validate(frame);
        if(errors.length)throw Error(errors[0]);
        if(!active||token!==generation)return;
        if(frame.frame_id!==lastId||frame.ts!==lastTs) {
          var restarted=lastId!==null&&(frame.frame_id<lastId||frame.ts<lastTs);
          lastId=frame.frame_id;lastTs=frame.ts;
          if(timed)lastProgress=options.now();
          report('live');
          options.onFrame(frame,restarted);
        } else if(timed&&lastProgress!==null&&options.now()-lastProgress>=options.freshMs)failed('源帧没有更新');
        else if(lastState!=='down')report('live');
      } catch(error) {
        if(active&&token===generation)failed(error.message);
      } finally {
        options.clearTimeout(deadline);
        if(controller===abort)controller=null;
        if(active&&token===generation)timer=options.setTimeout(function(){poll(token);},options.pollMs);
      }
    }
    function start(){stop();lastId=null;lastTs=null;lastProgress=null;lastState=null;active=true;poll(generation);}
    return {start:start,stop:stop};
  }
  root.VigiLensBoardHTTP={create:create};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.VigiLensBoardHTTP;
})(typeof globalThis!=='undefined'?globalThis:this);
