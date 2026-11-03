"use strict";
const assert=require('assert');
const transport=require('../board_transport.js');
const contract=require('../../../frontend/mock.js');
let tasks=new Map(),number=0,queue=[],frames=[],states=[];
const opts={pollMs:1000,freshMs:3000,AbortController,
 setTimeout:(f,delay)=>{tasks.set(++number,{f,delay});return number;},clearTimeout:id=>tasks.delete(id),
 fetch:async()=>{let next=queue.shift();if(next instanceof Error)throw next;return {ok:next.code===200,status:next.code,json:async()=>next.frame};},
 validate:contract.validateFrame,onFrame:(f,restarted)=>frames.push({f,restarted}),onState:s=>states.push(s)};
const client=transport.create(opts);
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const tick=async(next)=>{queue.push(next);let entry=[...tasks].find(x=>x[1].delay===1000);assert.ok(entry);tasks.delete(entry[0]);entry[1].f();await settle();};
(async()=>{
 let first=contract.mockFrame(50,{status:'normal'});queue.push({code:200,frame:first});client.start();await settle();
 assert.equal(frames.length,1);assert.deepEqual(frames[0].f,first);assert.equal(states.at(-1),'live');
 await tick({code:200,frame:first});assert.equal(frames.length,1);
 await tick({code:503});assert.equal(states.at(-1),'down');assert.equal(frames.length,1);
 await tick({code:200,frame:{broken:true}});assert.equal(states.at(-1),'down');assert.equal(frames.length,1);
 let reset=contract.mockFrame(0,{status:'adjust_posture'});reset.ts=0;
 reset.face.visible=0;reset.face.bbox=[0,0,0,0]; // Explicit no-face transport fixture.
 await tick({code:200,frame:reset});assert.equal(frames.length,2);assert.equal(frames[1].restarted,true);assert.equal(frames[1].f.face.visible,0);
 client.stop();assert.equal(tasks.size,0);
 let resolve;opts.fetch=()=>new Promise(r=>{resolve=r;});client.start();client.stop();
 resolve({ok:true,json:async()=>first});await settle();assert.equal(frames.length,2);assert.equal(tasks.size,0);
 console.log('BOARD_HTTP_TRANSPORT_PASS exact_frames_dedup_disconnect_invalid_restart_stop');
})().catch(e=>{console.error(e);process.exitCode=1;});
