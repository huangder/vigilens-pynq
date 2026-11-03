/* Exercise the actual packaged inline page with deterministic transport events.
 * Values below are explicit software fixtures, never physiological evidence. */
"use strict";
const assert=require("assert"),fs=require("fs"),vm=require("vm");
const html=fs.readFileSync(process.argv[2],"utf8");
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
assert.ok(!script.includes("HR_DISPLAY_BUNDLE"));
let now=1000,pending=[],intervals=[],response={error:true};
const nodes={};for(const id of ["state","metrics","hrValue","hrUnit","hrKind","hrNote","hrCard","preview","previewState"])nodes[id]={textContent:"",className:""};
const context=vm.createContext({document:{getElementById:id=>nodes[id]},Date:{now:()=>now},
 AbortController,console,JSON,
 setTimeout:(fn,delay)=>{pending.push({fn,delay});return fn;},clearTimeout:fn=>{pending=pending.filter(t=>t.fn!==fn);},
 setInterval:fn=>{intervals.push(fn);},
 fetch:async()=>{if(response.error)throw Error("fixture disconnect");return {ok:true,json:async()=>response.frame};}});
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
const tick=async(next)=>{response=next;now+=1000;const job=pending.find(t=>t.delay===1000);assert.ok(job);pending=pending.filter(t=>t!==job);job.fn();await flush();};
const frame=(id,hr,status="normal")=>({frame_id:id,ts:id,face:{visible:1},quality:{overall:1},vital:{hr_bpm:hr,hr_conf:.8},status,advice:"software fixture"});
(async()=>{
 vm.runInContext(script,context);await flush();
 assert.ok(nodes.preview.src.startsWith('/api/frame.jpg?'));
 nodes.preview.onload();assert.equal(nodes.preview.hidden,false);
 nodes.preview.onerror();assert.equal(nodes.preview.hidden,true);assert.ok(nodes.previewState.textContent.includes('等待相机恢复'));
 assert.equal(nodes.hrValue.textContent,"正在测量");
 await tick({frame:frame(1,72)});assert.equal(nodes.hrValue.textContent,"72.0");assert.equal(nodes.hrKind.textContent,"当前估计");
 await tick({frame:frame(2,null,"unreliable")});assert.equal(nodes.hrValue.textContent,"72.0");assert.ok(nodes.hrKind.textContent.includes("历史"));
 await tick({error:true});assert.equal(nodes.hrValue.textContent,"72.0");assert.ok(nodes.hrNote.textContent.includes("连接中断"));
 now+=5000;intervals.forEach(fn=>fn());assert.ok(nodes.hrNote.textContent.includes("7秒前"));
 await tick({frame:frame(3,74)});assert.equal(nodes.hrValue.textContent,"74.0");assert.equal(nodes.hrKind.textContent,"当前估计");
 await tick({frame:frame(0,null)});assert.equal(nodes.hrValue.textContent,"正在测量");
 console.log("PACKAGED_HR_PAGE_PASS: waiting/current/history/disconnect/aging/recovery/restart; software fixtures only");
})().catch(error=>{console.error(error);process.exitCode=1;});
