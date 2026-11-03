/* Injected inside the unchanged dashboard's closure only for board packaging.
 * Reuses its validators, cards, trends, alerts and history presentation. */
  var boardConfig=window.VigiLensBoardConfig,boardSession=null,boardPreviewTimer=null,boardWatchdog=null;
  var boardGeneration=0,boardPreviewAt=0;
  var boardHistoryStorage;try{boardHistoryStorage=window.localStorage;}catch(error){boardHistoryStorage=null;}
  var boardHrHistory=window.VigiLensHrHistory?window.VigiLensHrHistory.create(Object.assign({},boardConfig.hrHistory,{
    now:function(){return Date.now();},storage:boardHistoryStorage,key:'vigilens.hr.history.v1:'+location.host,source:location.host
  })):null;
  var boardHistoryView=boardHrHistory?window.VigiLensHrHistory.mount(document,boardHrHistory,boardConfig.hrHistory):null;
  var dashboardStop=stopAll,dashboardSource=renderSource,dashboardFrame=onFrame,dashboardCards=renderCards;
  var dashboardBuildCards=typeof buildCards==='function'?buildCards:null;
  if(dashboardBuildCards)buildCards=function(){
    dashboardBuildCards();var card=$('card_hr'),note=card&&card.querySelector('.note');
    if(note)note.remove();
  };
  var boardHrMotion=window.VigiLensBoardHrMotion.create({transitionMs:boardConfig.hrTransitionMs,
    viewportMs:boardConfig.pollMs,freshMs:boardConfig.freshMs});
  var boardAnimation=null,boardPaintAt=0;
  renderCards=function(frame,onlyMetric){
    dashboardCards(frame,onlyMetric);
    if(state.mode!=='board')return;
    var view=boardHrMotion.view(Date.now()),card=$('card_hr');
    if(card&&(view.animating||view.historyAnimating)){
      card.querySelector('.num').textContent=(view.current?view.value:view.historicalValue).toFixed(1);
      card.querySelector('.card-meta').textContent=view.current?'平滑过渡':'历史过渡';
    }
  };
  function boardAnimate(){
    if(state.mode!=='board'){boardAnimation=null;return;}
    var now=Date.now(),view=boardHrMotion.view(now);
    if(now-boardPaintAt>=1000/boardConfig.animationHz&&(view.animating||view.historyAnimating||view.scrolling)){
      boardPaintAt=now;renderCards(state.lastFrame,'hr');renderCharts('hr');
    }
    boardAnimation=window.requestAnimationFrame(boardAnimate);
  }
  function boardDrawHrContinuity(card,canvas,draw,getter,gapSeconds) {
    var view=window.VigiLensBoardHrTrend.annotations(state.points,getter,{
      sampleGapSeconds:gapSeconds,bridgeSeconds:boardConfig.hrTrendBridgeSeconds,
      holdSeconds:boardConfig.hrTrendHoldSeconds
    });
    draw(view.bridges,'#b4a28a',true);
    if(view.tail.length)draw([view.tail],'#b4a28a',true);
    canvas.dataset.bridges=String(view.bridges.length);
    canvas.dataset.historyTail=view.tail.length?'1':'0';
    canvas.title='实线为有效心率；虚线为缺测连接或历史延续，非当前测量';
    canvas.setAttribute('aria-label',canvas.getAttribute('aria-label')+'；'+canvas.title);
    if(view.tail.length)card.querySelector('.latest-label').textContent='历史延续 · '+Math.floor(view.age)+'秒';
    card.querySelector('.card-trend-label > span').title=canvas.title;
    card.querySelector('.card-trend-label > span').textContent='趋势 · 虚线为历史';
  }
  function stopBoardSource() {
    boardGeneration++;
    if(boardSession)boardSession.stop();
    boardSession=null;
    clearTimeout(boardPreviewTimer);clearInterval(boardWatchdog);
    boardPreviewTimer=null;boardWatchdog=null;boardPreviewAt=0;
    if(boardAnimation!==null)window.cancelAnimationFrame(boardAnimation);
    boardAnimation=null;boardPaintAt=0;boardHrMotion.reset();
    if(el.previewImage){el.previewImage.onload=null;el.previewImage.onerror=null;}
  }
  stopAll=function(){stopBoardSource();return dashboardStop.apply(null,arguments);};
  renderSource=function(){
    if(state.mode!=='board'){dashboardSource();return;}
    el.srcName.textContent='板端原生';el.srcPill.className='pill live';
  };
  onFrame=function(frame){
    if(state.mode==='board'&&boardHrHistory&&boardHrHistory.accept(frame,Date.now()))boardHistoryView.refresh();
    if(state.mode==='board')boardHrMotion.accept(frame,Date.now(),cardValue(frame,
      CARD_DEFS.filter(function(d){return d.key==='hr';})[0]));
    dashboardFrame(frame);
    if(state.mode==='board') {
      el.modeName.textContent='板端 PL + PS';
      el.gateVerdictSub.textContent=frame.advice+' · '+frame.reason;
    }
  };
  function boardPreview(token) {
    if(state.mode!=='board'||token!==boardGeneration)return;
    el.previewImage.onload=function(){
      if(state.mode!=='board'||token!==boardGeneration)return;
      boardPreviewAt=Date.now();
      updateCameraAspect(el.previewImage.naturalWidth,el.previewImage.naturalHeight);
      el.previewImage.classList.add('ready');el.videoPlaceholder.hidden=true;
      setPreviewBadge('ok','板端实时画面');el.previewFreshness.textContent='刚刚';
      el.previewSync.textContent='图像独立旁路';el.previewSync.className='badge';
      el.previewDebug.textContent='板端最新 JPEG；未提供同帧标注，不绘制定位框';
      boardPreviewTimer=setTimeout(function(){boardPreview(token);},boardConfig.previewMs);
    };
    el.previewImage.onerror=function(){
      if(state.mode!=='board'||token!==boardGeneration)return;
      el.previewImage.classList.remove('ready');el.videoPlaceholder.hidden=false;
      el.videoPlaceholderText.textContent='画面暂不可用，等待相机恢复';setPreviewBadge('warn','等待画面');
      boardPreviewTimer=setTimeout(function(){boardPreview(token);},boardConfig.pollMs);
    };
    el.previewImage.src='/api/frame.jpg?t='+Date.now();
  }
  connectWs=function connectBoard(){
    stopAll(true);
    state.points.length=0;state.lastFrame=null;hrDisplay.reset();state.mode='board';
    MAX_POINTS=Math.ceil(HISTORY_SECONDS*1000/boardConfig.pollMs)+1;
    state.serverSource='board-native';state.usesApiStatus=false;
    applyServerThresholds(boardConfig.thresholds);
    renderState(null);renderCards(null);renderGate(null);renderBlink(null);renderCharts();renderSource();
    el.wsUrl.value=location.origin+'/api/latest';el.measurementTime.textContent='等待板端首帧';
    setConn('','连接板端…');clearPreviewImage('等待板端画面');
    var token=boardGeneration;
    boardSession=window.VigiLensBoardHTTP.create({
      fetch:window.fetch.bind(window),AbortController:window.AbortController,
      now:function(){return Date.now();},
      setTimeout:window.setTimeout.bind(window),clearTimeout:window.clearTimeout.bind(window),
      pollMs:boardConfig.pollMs,freshMs:boardConfig.freshMs,validate:M.validateFrame,
      onFrame:function(frame,restarted){
        if(restarted){state.points.length=0;hrDisplay.reset();boardHrMotion.reset();}
        state.lastRxAt=Date.now();onFrame(frame);
      },
      onState:function(kind,message){
        if(kind==='live')setConn('live','板端已连接');
        else if(kind==='retrying')setConn('warn','读取重试中 · 等待新帧');
        else {
          setConn('down','板端数据暂不可用');
          if(!state.lastFrame||state.lastFrame.status!=='disconnected')onFrame(synthesizeDisconnected());
          el.gateVerdictSub.textContent='正在重试板端数据接口：'+message;
        }
      }
    });
    boardSession.start();boardPreview(token);
    boardAnimation=window.requestAnimationFrame(boardAnimate);
    boardWatchdog=setInterval(function(){
      if(state.mode!=='board')return;
      renderCards(state.lastFrame,'hr');
      if(boardPreviewAt&&Date.now()-boardPreviewAt>boardConfig.freshMs) {
        el.previewImage.classList.remove('ready');el.videoPlaceholder.hidden=false;
        el.videoPlaceholderText.textContent='画面已过期，等待恢复';setPreviewBadge('danger','预览中断');
      }
    },boardConfig.pollMs);
    log('系统','已启用板端 HTTP 实时监测；仅接收真实契约帧','#007aff');
  };
  syncWithServer=function(){setTimeout(connectWs,0);};
  window.addEventListener('pagehide',stopBoardSource);
