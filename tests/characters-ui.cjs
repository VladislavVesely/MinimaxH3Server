const fs=require('fs'),assert=require('node:assert/strict');
const html=fs.readFileSync('src/h3server/web/index.html','utf8'),app=fs.readFileSync('src/h3server/web/app.js','utf8');
assert.ok(html.includes('/assets/characters.js'));assert.ok(html.includes('id="character-cards"'));
assert.ok(html.includes('id="character-completions"'));assert.ok(html.includes('id="character-prompt-preview"'));
assert.match(app,/H3Characters\?\.create/);assert.match(app,/characters\?\.load\(/);assert.match(app,/characters\?\.reset\(/);assert.match(app,/characters\?\.reasons\(/);assert.match(app,/characters\?\.detach\(/);
console.log('Character library and authoring hooks are integrated');
