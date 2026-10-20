import {copyFile,readFile,appendFile,cp} from 'node:fs/promises';
import {existsSync} from 'node:fs';
for(const file of ['app.js','app.css','record-card.css','index.html','attachment-preview.js','workspace-mount.js','file-mentions.js','execution-details.js'])await copyFile(new URL('../bridge/'+file,import.meta.url),new URL('../../gateway/internal/server/static/'+file,import.meta.url));
// Keep this workstation's font setup local while preserving it during UI builds.
const localFonts=new URL('../bridge/fonts.local.css',import.meta.url);
if(existsSync(localFonts)){
  await appendFile(new URL('../../gateway/internal/server/static/app.css',import.meta.url),'\n'+await readFile(localFonts,'utf8'));
  await cp(new URL('../bridge/fonts/',import.meta.url),new URL('../../gateway/internal/server/static/fonts/',import.meta.url),{recursive:true});
}
