import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
let parts=[],lastCommit=false,conflict=false;
const ctx={TextEncoder,crypto:{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},fetch:async(url,options)=>{
 if(url==='/api/upload'){const body=JSON.parse(options.body);assert.ok(!/[\uD800-\uDBFF]$/.test(body.text));assert.ok(Buffer.byteLength(options.body)<2000000);if(conflict)return {ok:false,status:409};parts[body.part]=body.text;return {ok:true,status:200}}
 if(url==='/api/commit'){lastCommit=true;return {ok:true,json:async()=>({revision:2})}}
 if(url==='/api/workspace')return {ok:true,json:async()=>({revision:2,parts:parts.length})};
 const n=Number(new URL('http://test'+url).searchParams.get('part'));return {ok:true,json:async()=>({text:parts[n]})}
}};vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('async function fetchWorkspace(')),ctx);
const data={company:{notes:'x'.repeat(249970)+'😀 informação çã '.repeat(60000)},clients:[],proposals:[{documents:[{content:'YW5leG8='}]}],events:[]};
await ctx.uploadWorkspace(data,1);assert.ok(parts.length>1);assert.ok(lastCommit);assert.deepEqual(JSON.parse(parts.join('')),data);
const loaded=await ctx.fetchWorkspace();assert.equal(JSON.stringify(loaded.data),JSON.stringify(data));
conflict=true;lastCommit=false;assert.equal((await ctx.uploadWorkspace(data,1)).status,409);assert.equal(lastCommit,false);
console.log('Transporte validado: partes, Unicode, anexo, leitura e interrupção em conflito.');
