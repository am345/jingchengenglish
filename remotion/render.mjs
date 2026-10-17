import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {selectComposition, renderMedia} from '@remotion/renderer';
const [propsFile, publicDir, outputLocation, concurrency, crf] = process.argv.slice(2);
if (!outputLocation) throw new Error('Usage: node render.mjs props.json public-dir output.mp4 concurrency crf');
const inputProps = JSON.parse(fs.readFileSync(propsFile, 'utf8'));
const root = path.dirname(fileURLToPath(import.meta.url));
const serveUrl = await bundle({entryPoint: path.join(root,'src/index.tsx'), publicDir,
  outDir:path.join(path.dirname(outputLocation),'bundle')});
const composition = await selectComposition({serveUrl, id:'Lesson', inputProps});
await renderMedia({serveUrl, composition, inputProps, codec:'h264', outputLocation,
  pixelFormat:'yuv420p', concurrency:Number(concurrency), crf:Number(crf), overwrite:false});
console.log(outputLocation);
