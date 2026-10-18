'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const code=fs.readFileSync(path.join(root,'food_map/dist/app.js'),'utf8');
const html=fs.readFileSync(path.join(root,'food_map/dist/index.html'),'utf8');
const cutoff=code.indexOf("$('search').addEventListener");
assert.ok(cutoff>0,'The event bootstrap must remain separate from pure helpers');
const mapStatus={textContent:''};
const helpers=vm.runInNewContext(code.slice(0,cutoff)+';({normalizeFood,safeLink,loadMap});',{
  URL,document:{getElementById:id=>{assert.equal(id,'mapStatus');return mapStatus;}},
  fetch:async()=>({ok:true,json:async()=>({key:''})})
});
const original={id:'example',name:'\u793a\u4f8b\u9910\u9986',city:'\u4e0a\u6d77',category:'\u5496\u5561',note:'\u539f\u6587\u63a8\u8350',source_name:'\u6765\u6e90',source_url:'https://cachetide.top/posts/example',location:'121.4,31.2'};
const normalized=helpers.normalizeFood(original);
assert.equal(normalized.category,'Coffee');
assert.equal(normalized.source_name,'CacheTide');
assert.match(normalized.note,/original recommendation/);
assert.equal(normalized.name,original.name,'Do not invent translations of business proper names');
assert.equal(original.category,'\u5496\u5561','The external source object must not be modified');
assert.equal(helpers.safeLink('javascript:alert(1)'),null);
assert.equal(helpers.safeLink('http://example.org'),null);
assert.equal(helpers.safeLink('https://user:password@example.org'),null);
assert.equal(helpers.safeLink('https://example.org/place'),'https://example.org/place');
assert.match(html,/<html lang="en">/);
assert.match(code,/languageCode:'en'/,'Use the documented Amap 2.0 English-map option');
assert.doesNotMatch(html,/[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/);
for(const id of ['search','city','categories','results','map','reloadButton','about','closeAbout'])assert.ok(html.includes(`id="${id}"`));
helpers.loadMap().then(()=>{
  assert.match(mapStatus.textContent,/No Amap JS key/);
  console.log('Food-map English UI and legacy-feed compatibility: PASS');
}).catch(error=>{console.error(error);process.exitCode=1;});
