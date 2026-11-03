'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const H=require('../board_hr_trend.js');
const options={sampleGapSeconds:2,bridgeSeconds:30,holdSeconds:10};
const frame=(ts,value,status='normal')=>({ts,frame_id:ts,status,value});
const get=f=>f.value;

test('short null gaps receive only dashed endpoint annotations; frames stay unchanged',()=>{
  const frames=[frame(1,72),frame(2,null),frame(3,74)];
  const before=JSON.stringify(frames);
  assert.deepEqual(H.annotations(frames,get,options),{
    bridges:[[{ts:1,value:72},{ts:3,value:74}]],tail:[],age:0
  });
  assert.equal(JSON.stringify(frames),before);
});
test('valid adjacent samples require no bridge',()=>{
  assert.equal(H.annotations([frame(1,72),frame(2,73)],get,options).bridges.length,0);
});
test('missing frames can bridge within configured age, never across long absence',()=>{
  assert.equal(H.annotations([frame(1,72),frame(20,73)],get,options).bridges.length,1);
  assert.equal(H.annotations([frame(1,72),frame(32,73)],get,options).bridges.length,0);
});
test('tail is explicitly historical and expires instead of extending indefinitely',()=>{
  const frames=[frame(1,72),frame(5,null)];
  assert.deepEqual(H.annotations(frames,get,options).tail,[{ts:1,value:72},{ts:5,value:72}]);
  assert.deepEqual(H.annotations([...frames,frame(12,null)],get,options).tail,[]);
});
test('disconnect cancels a tail and cannot bridge to a recovered measurement',()=>{
  const frames=[frame(1,72),frame(2,null,'disconnected')];
  assert.deepEqual(H.annotations(frames,get,options).tail,[]);
  assert.deepEqual(H.annotations([...frames,frame(3,74)],get,options).bridges,[]);
});
test('producer timestamp or ID rollback resets presentation continuity',()=>{
  for(const restarted of [frame(0,75),{...frame(3,75),frame_id:0}]){
    assert.deepEqual(H.annotations([frame(1,72),frame(2,null),restarted],get,options).bridges,[]);
  }
});
test('no initial estimate means no historical values or artificial zero',()=>{
  const view=H.annotations([frame(1,null),frame(2,null)],get,options);
  assert.deepEqual(view,{bridges:[],tail:[],age:null});
});
test('measurement eligibility is supplied by the original card gate',()=>{
  const frames=[frame(1,72),frame(2,73,'unreliable'),frame(3,74)];
  const view=H.annotations(frames,f=>f.status==='unreliable'?null:get(f),options);
  assert.equal(view.bridges.length,1);
  assert.deepEqual(view.bridges[0].map(p=>p.value),[72,74]);
});
