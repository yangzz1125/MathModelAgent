import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeHistory,upsertMessage} from '../src/utils/messageIndex.ts';
test('10000 updates replace one indexed message',()=>{
  const rows=[],index=new Map();
  for(let i=0;i<10000;i++) upsertMessage(rows,index,{id:'a',content:String(i)});
  assert.equal(rows.length,1);assert.equal(rows[0].content,'9999');assert.equal(index.get('a'),0);
});
test('history does not overwrite newer live updates',()=>{
  const rows=mergeHistory([{id:'a',content:'new'}],[{id:'a',content:'old'},{id:'b',content:'history'}]);
  assert.equal(rows.find(x=>x.id==='a').content,'new');assert.equal(rows.length,2);
});
test('out of order insertion repairs positions',()=>{
  const rows=[],index=new Map();
  for(const day of [3,1,2]) upsertMessage(rows,index,{id:String(day),created_at:`2026-01-0${day}T00:00:00Z`});
  assert.deepEqual(rows.map(x=>x.id),['1','2','3']);
  upsertMessage(rows,index,{id:'3',content:'latest'});assert.equal(rows[2].content,'latest');
});
