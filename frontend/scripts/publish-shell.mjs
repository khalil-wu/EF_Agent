import {copyFile} from 'node:fs/promises';
for(const file of ['app.js','app.css','record-card.css','index.html','attachment-preview.js','workspace-mount.js','file-mentions.js','execution-details.js'])await copyFile(new URL('../bridge/'+file,import.meta.url),new URL('../../gateway/internal/server/static/'+file,import.meta.url));
