import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export function runWorker(action:string,args:any,signal?:AbortSignal):Promise<any>{
 const file=fileURLToPath(new URL('../processor.py',import.meta.url));
 return new Promise((resolve,reject)=>{
  const child=spawn(process.env.SPRITE_PYTHON||'python3',[file,action],{stdio:['pipe','pipe','pipe'],signal});
  const output:Buffer[]=[];const errors:Buffer[]=[];let bytes=0;let settled=false;
  const end=(error?:Error,value?:any)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value);};
  const timer=setTimeout(()=>{child.kill('SIGTERM');end(new Error('Sprite processing timed out; original media preserved'));},300000);timer.unref();
  child.on('error',()=>end(new Error('Cannot run sprite worker; check Python/NumPy/Pillow and FFmpeg/ffprobe. No automatic installation.')));
  child.stdin.on('error',()=>{});
  child.stdout.on('data',(b:Buffer)=>{bytes+=b.length;if(bytes>2*1024*1024){child.kill();end(new Error('Sprite worker output limit'));}else output.push(b);});
  child.stderr.on('data',(b:Buffer)=>{if(errors.reduce((n,x)=>n+x.length,0)<16384)errors.push(b);});
  child.on('close',code=>{
   if(code!==0){let error='Sprite worker failed; original inputs preserved';try{error=JSON.parse(Buffer.concat(errors).toString()).error??error;}catch{}end(new Error(error));return;}
   try{end(undefined,JSON.parse(Buffer.concat(output).toString()));}catch{end(new Error('Invalid sprite worker result'));}
  });
  child.stdin.end(JSON.stringify(args));
 });
}
