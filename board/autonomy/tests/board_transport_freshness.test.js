'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const Transport=require('../board_transport.js');
const Contract=require('../../../frontend/mock.js');
function fixture(){
  let now=0,tasks=new Map(),id=0,queue=[],frames=[],states=[];
  const options={pollMs:200,freshMs:3000,now:()=>now,AbortController,
    setTimeout:(fn,delay)=>{tasks.set(++id,{fn,delay});return id;},clearTimeout:key=>tasks.delete(key),
    fetch:async()=>{const next=queue.shift();if(next instanceof Error)throw next;
      return {ok:next.code===200,status:next.code,json:async()=>next.frame};},
    validate:Contract.validateFrame,onFrame:(f,restart)=>frames.push({f,restart}),onState:state=>states.push(state)};
  const client=Transport.create(options);
  async function settle(){for(let i=0;i<15;i++)await Promise.resolve();}
  async function start(frame){queue.push({code:200,frame});client.start();await settle();}
  async function tick(at,next){now=at;queue.push(next);const entry=[...tasks].find(x=>x[1].delay===200);
    assert.ok(entry);tasks.delete(entry[0]);entry[1].fn();await settle();}
  return {client,start,tick,frames,states};
}
test('a transient failed poll does not turn every metric into disconnected data',async()=>{
  const f=fixture(),frame=Contract.mockFrame(50,{status:'normal'}),original=JSON.stringify(frame);
  await f.start(frame);await f.tick(200,{code:503});
  assert.deepEqual(f.states,['live','retrying']);assert.equal(f.frames.length,1);
  await f.tick(400,{code:200,frame});assert.equal(f.frames.length,1);
  assert.equal(JSON.stringify(frame),original);assert.equal(f.states.at(-1),'live');
  await f.tick(600,{code:200,frame:Contract.mockFrame(51,{status:'unreliable'})});
  assert.equal(f.frames.length,2);assert.equal(f.frames[1].f.status,'unreliable');
  assert.deepEqual(f.frames[1].f.vital,Contract.mockFrame(51,{status:'unreliable'}).vital);
  f.client.stop();
});
test('persistent loss times out from last new frame and duplicates cannot restore freshness',async()=>{
  const f=fixture(),frame=Contract.mockFrame(50,{status:'normal'});await f.start(frame);
  await f.tick(1000,{code:503});await f.tick(2999,{code:503});
  assert.equal(f.states.filter(s=>s==='retrying').length,1);
  await f.tick(3000,{code:503});assert.equal(f.states.at(-1),'down');
  await f.tick(3200,{code:200,frame});assert.equal(f.states.at(-1),'down');
  assert.equal(f.frames.length,1);
  await f.tick(3400,{code:200,frame:Contract.mockFrame(51,{status:'normal'})});
  assert.equal(f.states.at(-1),'live');assert.equal(f.frames.length,2);
  f.client.stop();
});
test('a frozen source still times out even if HTTP stays 200',async()=>{
  const f=fixture(),frame=Contract.mockFrame(50,{status:'normal'});await f.start(frame);
  for(const at of [1000,2000,3000,4000])await f.tick(at,{code:200,frame});
  assert.deepEqual(f.states,['live','down']);assert.equal(f.frames.length,1);f.client.stop();
});
