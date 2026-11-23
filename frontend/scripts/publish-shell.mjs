import {copyFile, readFile, writeFile} from 'node:fs/promises';
for(const file of ['app.js','app.css','record-card.css','index.html','attachment-preview.js','workspace-mount.js','file-mentions.js','execution-details.js','ambient.js'])await copyFile(new URL('../bridge/'+file,import.meta.url),new URL('../../gateway/internal/server/static/'+file,import.meta.url));
// Source maps embed original working-tree bytes; normalize CRLF for reproducible builds.
const mapURL = new URL('../../gateway/internal/server/static/transcript-ui.js.map', import.meta.url);
const map = JSON.parse(await readFile(mapURL, 'utf8'));
map.sourcesContent = map.sourcesContent.map(text => typeof text === 'string' ? text.replace(/\r\n/g, '\n') : text);
await writeFile(mapURL, JSON.stringify(map));
