"use strict";
const test=require('node:test'),assert=require('node:assert/strict');
const history=require('../board_hr_history.js');
function fixture(){
  let now=10000,stored={},writes=0;
  const storage={getItem:k=>stored[k]||null,setItem:(k,v)=>{stored[k]=v;writes++;}};
  const options={now:()=>now,storage,key:'test',source:'board',minBpm:60,minimumQuality:.75,
    maxRecords:3,retentionMs:10000,intervalMs:1000};
  return {options,storage,advance:n=>{now=n;},writes:()=>writes};
}
function frame(id,bpm=72,status='normal'){
  return {frame_id:id,ts:id,face:{visible:1},quality:{overall:.9},vital:{hr_bpm:bpm,hr_conf:.8},status};
}
test('store genuine eligible observations, deduplicate and enforce interval',()=>{
  const f=fixture(),h=history.create(f.options);
  assert.equal(h.accept(frame(1),10000),true);
  assert.equal(h.accept(frame(1),12000),false);
  assert.equal(h.accept(frame(2,null),12000),false);
  assert.equal(h.accept(frame(3,59.9),12000),false);
  assert.equal(h.accept(frame(4,72,'unreliable'),12000),false);
  assert.equal(h.accept({...frame(5),quality:{overall:.5}},12000),false);
  assert.equal(h.accept(frame(6),10500),false);
  assert.equal(h.list(null,null).length,1);
  assert.equal(f.writes(),1);
});
test('reload persists, filters ranges, caps count and separates restarted sessions',()=>{
  const f=fixture();let h=history.create(f.options);
  for(let i=1;i<=4;i++){f.advance(9000+i*1000);h.accept(frame(i,70+i),f.options.now());}
  assert.deepEqual(h.list(null,null).map(r=>r.bpm),[72,73,74]);
  h=history.create(f.options);assert.equal(h.list(12000,13000).length,2);
  assert.equal(h.accept(frame(4,74),13000),false);
  const oldSession=h.list(null,null)[0].session;
  f.advance(14000);assert.equal(h.accept(frame(0,75),14000),true);
  assert.notEqual(h.list(null,null).at(-1).session,oldSession);
  assert.equal(h.list(null,null).length,3);
  const csv=h.csv(13000,14000);
  assert.ok(csv.includes('observed_time'));assert.ok(csv.includes('"74"'));assert.ok(csv.includes('"75"'));
  f.advance(25000);assert.equal(h.list(null,null).length,0);
});
test('disconnect placeholders do not create a session or re-save an old frame',()=>{
  const f=fixture(),h=history.create(f.options);
  assert.equal(h.accept(frame(20),10000),true);
  const session=h.list(null,null)[0].session;
  assert.equal(h.accept(frame(999,null,'disconnected'),12000),false);
  assert.equal(h.accept(frame(20),13000),false);
  assert.equal(h.accept(frame(21),14000),true);
  f.advance(14000);
  assert.deepEqual(h.list(null,null).map(r=>r.frameId),[20,21]);
  assert.equal(h.list(null,null)[1].session,session);
});
test('storage failure and clock rollback do not block live measurements',()=>{
  const f=fixture();f.options.storage={getItem:()=>{throw Error('blocked');},setItem:()=>{throw Error('quota');}};
  const h=history.create(f.options);assert.equal(h.persistent(),false);
  assert.equal(h.accept(frame(1),10000),true);
  f.advance(9000);assert.equal(h.accept(frame(2),9000),true);
  assert.equal(h.list(null,null).length,1);
});
test('history UI renders safe text, binds range selection and exports',()=>{
  const f=fixture(),h=history.create(f.options);h.accept(frame(1),10000);
  function node(){return {value:'',open:true,children:[],events:{},textContent:'',
    appendChild(n){this.children.push(n);},addEventListener(k,v){this.events[k]=v;},click(){this.clicked=true;}};}
  const ids={};for(const id of ['hrHistoryPanel','hrHistoryFrom','hrHistoryTo','hrHistoryRows','hrHistoryCount','hrHistoryExport'])ids[id]=node();
  const doc={getElementById:id=>ids[id],createElement:()=>node()};
  const ui=history.mount(doc,h,{listLimit:100});
  assert.equal(ids.hrHistoryRows.children.length,1);
  assert.equal(ids.hrHistoryRows.children[0].children[1].textContent,'72.0');
  assert.ok(ids.hrHistoryCount.textContent.includes('1 条有效观测'));
  assert.equal(typeof ids.hrHistoryFrom.events.change,'function');
  ids.hrHistoryExport.events.click();ui.refresh();
});
