'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const Motion=require('../board_hr_motion.js');
const make=()=>Motion.create({transitionMs:1000,viewportMs:200,freshMs:3000});
const frame=(id,ts=id,status='normal')=>({frame_id:id,ts,status});
test('first current value starts without a fabricated zero and later changes interpolate',()=>{
  const m=make();m.accept(frame(1),0,72);
  assert.equal(m.view(0).value,72);
  m.accept(frame(2),1000,84);
  assert.equal(m.view(1000).value,72);
  assert.equal(m.view(1500).value,78);
  assert.equal(m.view(2000).value,84);
  assert.equal(m.view(1500).target,84);
});
test('transition is monotonic and bounded and accepts a changed target mid-animation',()=>{
  const m=make();m.accept(frame(1),0,72);m.accept(frame(2),1000,84);
  const values=Array.from({length:11},(_,i)=>m.view(1000+i*100).value);
  assert.ok(values.every((v,i)=>v>=72&&v<=84&&(i===0||v>=values[i-1])));
  const before=m.view(1500).value;m.accept(frame(3),1500,68);
  assert.equal(m.view(1500).value,before);
  assert.ok(m.view(1750).value<before);
});
test('duplicate target frames do not keep restarting the transition',()=>{
  const m=make();m.accept(frame(1),0,72);m.accept(frame(2),1000,84);
  m.accept(frame(3),1200,84);m.accept(frame(4),1400,84);
  assert.equal(m.view(2000).value,84);
});
test('viewport moves between packet timestamps without advancing beyond measured time',()=>{
  const m=make();m.accept(frame(1,10),0,72);m.accept(frame(2,10.2),200,72);
  assert.equal(m.time(200),10);
  assert.equal(m.time(300),10.1);
  assert.equal(m.time(400),10.2);
  assert.equal(m.time(9999),10.2);
});
test('missing HR and disconnect do not become animated current measurements',()=>{
  const m=make();m.accept(frame(1),0,72);m.accept(frame(2),1000,null);
  assert.equal(m.view(1100).current,false);assert.equal(m.view(1100).value,null);
  m.accept(frame(3,3,'disconnected'),1200,null);
  assert.equal(m.view(1300).value,null);
});
test('brief missing data can finish a labeled historical transition without a current value',()=>{
  const m=make();m.accept(frame(1),0,72);m.accept(frame(2),1000,84);
  m.accept(frame(3),1200,null);
  const view=m.view(1500);
  assert.equal(view.current,false);assert.equal(view.value,null);
  assert.equal(view.historicalValue,78);assert.equal(view.historicalTarget,84);
  assert.equal(view.historyAnimating,true);
  assert.equal(m.view(2500).historyAnimating,false);
});
test('long absence, restart and explicit reset cannot animate from an old subject value',()=>{
  const m=make();m.accept(frame(1),0,72);m.accept(frame(2),5000,95);
  assert.equal(m.view(5000).value,95);
  m.accept(frame(0,0),5100,60);assert.equal(m.view(5100).value,60);
  m.reset();assert.equal(m.time(5200),null);assert.equal(m.view(5200).value,null);
});
