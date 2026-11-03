/* Execute the injected board renderer with synthetic contracts and a fake canvas. */
'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const H=require('../board_hr_trend.js');
const Motion=require('../board_hr_motion.js');
const History=require('../board_hr_history.js');
const source=fs.readFileSync(path.join(__dirname,'../board_dashboard_adapter.js'),'utf8');
function run(frames){
  const context=vm.createContext({state:{points:frames},stopAll(){},renderSource(){},onFrame(){},renderCards(){},
    window:{VigiLensBoardConfig:{hrTrendBridgeSeconds:30,hrTrendHoldSeconds:10,
            hrTransitionMs:1000,pollMs:200,freshMs:3000},
            VigiLensBoardHrTrend:H,VigiLensBoardHrMotion:Motion,addEventListener(){}}});
  vm.runInContext(source,context);
  const latest={},heading={},attributes={'aria-label':'心率趋势，2个有效样本'},drawn=[];
  const canvas={dataset:{samples:'2'},setAttribute(k,v){attributes[k]=v},getAttribute(k){return attributes[k]}};
  const card={querySelector(selector){return selector==='.latest-label'?latest:heading}};
  context.boardDrawHrContinuity(card,canvas,(segments,color,dashed)=>drawn.push({segments,color,dashed}),f=>f.value,2);
  return {canvas,latest,heading,attributes,drawn:JSON.parse(JSON.stringify(drawn))};
}
test('injected renderer draws dashed bridges without changing sample count or missing values',()=>{
  const frames=[{ts:1,frame_id:1,value:72},{ts:2,frame_id:2,value:null},{ts:3,frame_id:3,value:74}];
  const before=JSON.stringify(frames),view=run(frames);
  assert.equal(view.drawn[0].dashed,true);
  assert.equal(view.drawn[0].segments.length,1);
  assert.equal(view.canvas.dataset.bridges,'1');
  assert.equal(view.canvas.dataset.samples,'2');
  assert.equal(JSON.stringify(frames),before);
  assert.ok(view.heading.textContent.includes('虚线为历史'));
  assert.ok(view.attributes['aria-label'].includes('非当前测量'));
});
test('injected historical tail is labeled and is removed on disconnect',()=>{
  const frames=[{ts:1,frame_id:1,value:72},{ts:4,frame_id:4,value:null}];
  const view=run(frames);
  assert.equal(view.canvas.dataset.historyTail,'1');
  assert.equal(view.latest.textContent,'历史延续 · 3秒');
  const stopped=run([...frames,{ts:4,frame_id:4,value:null,status:'disconnected'}]);
  assert.equal(stopped.canvas.dataset.historyTail,'0');
  assert.equal(stopped.drawn.length,1); // bridge draw with an empty list, no tail
});

test('actual injected card animates without recreating the removed note',()=>{
  let now=0,context;
  const nodes={'.num':{},'.card-meta':{},'.note':{}};
  const card={querySelector:s=>nodes[s]};
  context=vm.createContext({state:{mode:'board',points:[]},Date:{now:()=>now},
    stopAll(){},renderSource(){},renderCards:f=>{nodes['.num'].textContent=String(f.vital.hr_bpm);},
    onFrame:f=>{context.state.lastFrame=f;context.renderCards(f);},
    cardValue:f=>f.vital.hr_bpm,CARD_DEFS:[{key:'hr'}],$:()=>card,
    el:{modeName:{},gateVerdictSub:{}},
    window:{VigiLensBoardConfig:{hrTransitionMs:1000,pollMs:200,freshMs:3000},
      VigiLensBoardHrMotion:Motion,addEventListener(){}}});
  vm.runInContext(source,context);
  const frame=(id,hr)=>({frame_id:id,ts:id,status:'normal',vital:{hr_bpm:hr},advice:'fixture',reason:'fixture'});
  context.onFrame(frame(1,72));now=1000;const target=frame(2,84);context.onFrame(target);
  assert.equal(nodes['.num'].textContent,'72.0');
  assert.equal(nodes['.card-meta'].textContent,'平滑过渡');
  assert.equal(nodes['.note'].textContent,undefined);
  now=1500;context.renderCards(target);assert.equal(nodes['.num'].textContent,'78.0');
  assert.equal(target.vital.hr_bpm,84); // alert/contract input is untouched
});

test('actual board card builder removes the HR note element',()=>{
  let removed=false,built=0;
  const note={remove(){removed=true;}};
  const card={querySelector:s=>s==='.note'&&!removed?note:null};
  const context=vm.createContext({state:{},stopAll(){},renderSource(){},onFrame(){},renderCards(){},
    buildCards(){built++;},$:()=>card,
    window:{VigiLensBoardConfig:{hrTransitionMs:1000,pollMs:200,freshMs:3000},
      VigiLensBoardHrMotion:Motion,addEventListener(){}}});
  vm.runInContext(source,context);context.buildCards();
  assert.equal(built,1);assert.equal(card.querySelector('.note'),null);
});

test('board adapter records eligible incoming frames while keeping nulls and transport input unchanged',()=>{
  let now=10000,saved=null;
  const cfg={hrTransitionMs:1000,pollMs:200,freshMs:3000,
    hrHistory:{retentionMs:86400000,maxRecords:5000,intervalMs:1000,listLimit:100,minBpm:60,minimumQuality:.75}};
  const context=vm.createContext({state:{mode:'board',points:[]},Date:{now:()=>now},location:{host:'board.local'},
    document:{getElementById:()=>null},stopAll(){},renderSource(){},renderCards(){},onFrame(){},
    cardValue:f=>f.vital.hr_bpm,CARD_DEFS:[{key:'hr'}],el:{modeName:{},gateVerdictSub:{}},
    window:{VigiLensBoardConfig:cfg,VigiLensBoardHrMotion:Motion,VigiLensHrHistory:History,
      localStorage:{getItem:()=>saved,setItem:(_key,value)=>{saved=value;}},addEventListener(){}}});
  vm.runInContext(source,context);
  const frame=(id,bpm)=>({frame_id:id,ts:id,status:'normal',face:{visible:1},quality:{overall:.9},
    vital:{hr_bpm:bpm,hr_conf:bpm===null?null:.8},advice:'fixture',reason:'fixture'});
  const current=frame(1,72),missing=frame(2,null),low=frame(3,59.9);
  const before=[current,missing,low].map(f=>JSON.stringify(f));
  context.onFrame(current);now+=1000;context.onFrame(missing);now+=1000;context.onFrame(low);
  assert.deepEqual([current,missing,low].map(f=>JSON.stringify(f)),before);
  const records=JSON.parse(saved).records;
  assert.equal(records.length,1);assert.equal(records[0].bpm,72);
});

test('HR animation updates only its own card instead of repainting all metrics',()=>{
  let now=0,rendered=[],charts=[];
  const card={querySelector:()=>({})};
  const context=vm.createContext({state:{mode:'board',lastFrame:{}},Date:{now:()=>now},
    stopAll(){},renderSource(){},onFrame(){},renderCards:(frame,only)=>rendered.push(only),
    renderCharts:only=>charts.push(only),$:()=>card,
    window:{VigiLensBoardConfig:{hrTransitionMs:1000,pollMs:200,freshMs:3000,animationHz:30},
      VigiLensBoardHrMotion:Motion,requestAnimationFrame:()=>1,addEventListener(){}}});
  vm.runInContext(source,context);
  context.boardHrMotion.accept({frame_id:1,ts:1,status:'normal'},0,72);
  context.boardHrMotion.accept({frame_id:2,ts:2,status:'normal'},1000,84);
  now=1500;context.boardAnimate();
  assert.deepEqual(rendered,['hr']);assert.deepEqual(charts,['hr']);
});
