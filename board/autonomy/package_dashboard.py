"""Reuse current B-line assets; generate only the board transport adapter."""
from pathlib import Path
import hashlib,json,math,re,shutil

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent

def board_layout_css(style):
    """Reuse the compact layout already authored for filming, with live data."""
    start='@media (min-width: 1100px) and (min-height: 600px) {'
    if style.count(start)!=1:raise ValueError('compact dashboard layout changed')
    media=style[style.index(start):]
    end=media.find('\n}')
    if end<0:raise ValueError('compact dashboard layout is incomplete')
    media=media[:end+2].replace('body.recording-mode','body.board-layout')
    return ("/* Layout from frontend/style.css; board metrics remain live. */\n"
            "html { scrollbar-gutter: stable; }\n"
            "body.board-layout #card_rr, body.board-layout .developer-tools,\n"
            "body.board-layout #btnStart, body.board-layout .badge-stack,\n"
            "body.board-layout .preview-caption,\n"
            "body.board-layout #card_hr .note { display: none; }\n"+media+'\n')

def package_dashboard(output,config):
    frontend=ROOT/'frontend';target=output/'ui';target.mkdir(parents=True,exist_ok=True)
    html=(frontend/'index.html').read_text(encoding='utf-8')
    app=(frontend/'app.js').read_text(encoding='utf-8')
    declaration=re.search(r'var THRESHOLDS = \{(.*?)\n  \};',app,re.S)
    if not declaration:raise ValueError('dashboard threshold declaration changed')
    keys=re.findall(r'^\s*(\w+):',declaration.group(1),re.M)
    thresholds={key:config[key] for key in keys}
    metrics_hz=float(config['board_vision']['dashboard_metrics_hz'])
    if not math.isfinite(metrics_hz) or not 0<metrics_hz<=float(config['fps_nominal']):
        raise ValueError('invalid board dashboard_metrics_hz')
    config_js={'thresholds':thresholds,'pollMs':1000/metrics_hz,
               'previewMs':1000/float(config['board_vision']['preview_hz']),'freshMs':1000*float(config['ws_disconnect_timeout_s'])}
    config_js.update(hrTrendBridgeSeconds=float(config['board_vision']['hr_trend_bridge_seconds']),
                     hrTrendHoldSeconds=float(config['board_vision']['hr_trend_hold_seconds']),
                     hrTransitionMs=float(config['board_vision']['hr_display_transition_ms']),
                     animationHz=float(config['board_vision']['dashboard_animation_hz']))
    history=config['board_vision']
    history_values={k:history[k] for k in ('hr_history_retention_seconds','hr_history_max_records',
                                          'hr_history_interval_seconds','hr_history_list_limit')}
    if (not all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in history_values.values()) or
            any(type(history_values[k]) is not int for k in ('hr_history_max_records','hr_history_list_limit'))):
        raise ValueError('invalid board HR history limits')
    config_js['hrHistory']={'retentionMs':1000*history_values['hr_history_retention_seconds'],
        'maxRecords':history_values['hr_history_max_records'],'intervalMs':1000*history_values['hr_history_interval_seconds'],
        'listLimit':history_values['hr_history_list_limit'],'minBpm':float(config['hr_report_min_bpm']),
        'minimumQuality':float(config['vital_require_quality'])}
    if (not all(math.isfinite(config_js[k]) for k in ('hrTrendHoldSeconds','hrTrendBridgeSeconds')) or
            not 0<config_js['hrTrendHoldSeconds']<=config_js['hrTrendBridgeSeconds']):
        raise ValueError('invalid board HR trend annotation intervals')
    if (not math.isfinite(config_js['hrTransitionMs']) or config_js['hrTransitionMs']<=0 or
            not math.isfinite(config_js['animationHz']) or not 0<config_js['animationHz']<=60):
        raise ValueError('invalid board HR animation configuration')
    bootstrap='  if (document.readyState === "loading")'
    live_branch='} else if (state.mode !== "ws") {'
    compact_labels='state.mode === "recording" ? compactLabels[g.key] : g.label'
    recording_query='var requestedScene = new URLSearchParams(location.search).get("recording");'
    if any(app.count(anchor)!=1 for anchor in (bootstrap,live_branch,compact_labels,recording_query)):
        raise ValueError('dashboard integration anchors changed')
    adapter=(HERE/'board_dashboard_adapter.js').read_text(encoding='utf-8')
    app=app.replace(live_branch,'} else if (state.mode !== "ws" && state.mode !== "board") {')
    app=app.replace(compact_labels,'(state.mode === "recording" || state.mode === "board") ? compactLabels[g.key] : g.label')
    app=app.replace(recording_query,'var requestedScene = null; // Board page always uses live measurements.')
    app=app.replace(bootstrap,adapter+'\n'+bootstrap)
    cards_start=app.index('  function renderCards(frame) {')
    cards_end=app.index('  /* ---------------------------------------------------------------- 曲线 */',cards_start)
    cards=app[cards_start:cards_end]
    if cards.count('    CARD_DEFS.forEach(function (d) {')!=1:
        raise ValueError('dashboard card rendering anchor changed')
    cards=cards.replace('  function renderCards(frame) {','  function renderCards(frame, onlyMetric) {')
    cards=cards.replace('    CARD_DEFS.forEach(function (d) {',
                        '    CARD_DEFS.forEach(function (d) {\n      if(onlyMetric&&d.key!==onlyMetric)return;')
    app=app[:cards_start]+cards+app[cards_end:]
    chart_anchor='      draw(primary, "#a4acb6", false);'
    ear_anchor='return f.behavior.ear_left;'
    if app.count(chart_anchor)!=1 or app.count(ear_anchor)!=1:
        raise ValueError('dashboard chart/EAR integration anchors changed')
    app=app.replace(chart_anchor,chart_anchor+'\n'+
        '      if(d.key==="hr"&&state.mode==="board")boardDrawHrContinuity(card,canvas,draw,'+
        'function(frame){return cardValue(frame,d);},2*MOCK_PERIOD_MS/1000);')
    loop_anchor='  function renderCharts() {\n    CARD_DEFS.forEach(function (d) {'
    time_anchor='      var endTs = state.points[state.points.length - 1].ts;'
    if app.count(loop_anchor)!=1 or app.count(time_anchor)!=1:
        raise ValueError('dashboard animation integration anchors changed')
    app=app.replace(loop_anchor,'  function renderCharts(onlyMetric) {\n    CARD_DEFS.forEach(function (d) {\n'+
        '      if(onlyMetric&&d.key!==onlyMetric)return;')
    app=app.replace(time_anchor,time_anchor+'\n'+
        '      if(d.key==="hr"&&state.mode==="board"&&boardHrMotion.time(Date.now())!==null)endTs=boardHrMotion.time(Date.now());')
    metadata_anchor='      canvas.dataset.segments = String(primary.length);'
    if app.count(metadata_anchor)!=1:raise ValueError('chart metadata anchor changed')
    app=app.replace(metadata_anchor,metadata_anchor+'\n'+
        '      canvas.dataset.bridges="0";canvas.dataset.historyTail="0";')
    app=app.replace(ear_anchor,'return 0.5*(f.behavior.ear_left+f.behavior.ear_right);')
    app=app.replace('EAR 眼睛纵横比','EAR 双眼均值')
    app=app.replace('实时页面已就绪：请点击“连接 WebSocket”；当前不自动播放 Mock','板端仪表盘已就绪，自动连接真实数据；未启用 Mock')
    scripts=re.findall(r'<script src="([^"]+)"></script>',html)
    for name in ['style.css',*scripts]:
        if name=='app.js':continue
        shutil.copyfile(frontend/name,target/name)
    shutil.copytree(frontend/'icons',target/'icons',dirs_exist_ok=True)
    html=html.replace('<body>','<body class="board-layout">')
    html=html.replace('<link rel="stylesheet" href="style.css">',
        '<link rel="stylesheet" href="style.css">\n<link rel="stylesheet" href="board_layout.css">')
    html=html.replace('<script src="app.js"></script>',
        '<script src="board_config.js"></script>\n<script src="board_transport.js"></script>\n'+
        '<script src="board_hr_trend.js"></script>\n<script src="app.js"></script>')
    html=html.replace('<script src="board_hr_trend.js"></script>',
        '<script src="board_hr_trend.js"></script>\n<script src="board_hr_motion.js"></script>')
    html=html.replace('<script src="app.js"></script>',
        '<script src="board_hr_history.js"></script>\n<script src="app.js"></script>')
    history_html='''<section style="margin:16px;padding:16px;border:1px solid #4c5564;border-radius:12px">
<details id="hrHistoryPanel"><summary style="cursor:pointer;font-size:18px">历史心率 · 当前浏览器</summary>
<p>仅保存通过门控的当前有效观测，按配置限制保存时间与条数；历史记录不表示当前测量。观测时间为本机接收时间，未做临床精度验证。</p>
<label>开始 <input type="datetime-local" id="hrHistoryFrom"></label>
<label>结束 <input type="datetime-local" id="hrHistoryTo"></label>
<button id="hrHistoryExport" type="button">导出筛选结果 CSV</button><p id="hrHistoryCount" aria-live="polite"></p>
<div style="overflow:auto;max-height:360px"><table style="width:100%;text-align:left"><thead><tr><th>观测时间</th><th>BPM</th><th>谱置信指标</th><th>来源 / 会话</th></tr></thead><tbody id="hrHistoryRows"></tbody></table></div>
</details></section>'''
    # Place controls before script execution so the adapter can bind them.
    html=html.replace('<script src="board_config.js"></script>',history_html+'\n<script src="board_config.js"></script>')
    html=html.replace('连接 WebSocket','连接板端').replace('WebSocket 地址','板端数据接口')
    html=html.replace('A 线同源视频预览','板端实时相机预览').replace('实时预览需 A 线持续发送同源 JPEG。','图像与指标均来自开发板。')
    (target/'index.html').write_text(html,encoding='utf-8',newline='\n')
    (target/'app.js').write_text(app,encoding='utf-8',newline='\n')
    (target/'board_layout.css').write_text(board_layout_css((frontend/'style.css').read_text(encoding='utf-8')),
        encoding='utf-8',newline='\n')
    (target/'board_config.js').write_text('window.VigiLensBoardConfig='+json.dumps(config_js,ensure_ascii=True)+';\n',encoding='ascii',newline='\n')
    shutil.copyfile(HERE/'board_transport.js',target/'board_transport.js')
    shutil.copyfile(HERE/'board_hr_trend.js',target/'board_hr_trend.js')
    shutil.copyfile(HERE/'board_hr_motion.js',target/'board_hr_motion.js')
    shutil.copyfile(HERE/'board_hr_history.js',target/'board_hr_history.js')
    return ['ui/'+p.relative_to(target).as_posix() for p in sorted(target.rglob('*')) if p.is_file()]
