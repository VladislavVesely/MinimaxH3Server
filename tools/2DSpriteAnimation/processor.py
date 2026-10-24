"""Local, quota-free sprite preparation/extraction. No inference or credentials.

Soft alpha from one chroma background is an estimate, not recovered ground truth.
All exports use a shared canvas; no per-frame recentering, mirroring or forced seam.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import warnings

import numpy as np
from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = 32_000_000
warnings.simplefilter('error', Image.DecompressionBombWarning)
CANDIDATES = ['#00ff00', '#ff00ff', '#00ffff', '#0000ff', '#ff8000', '#ff0000', '#80ff00', '#8000ff']

def rgb(value):
    if not isinstance(value,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value):
        raise ValueError('Colour must be #RRGGBB')
    return np.array([int(value[i:i+2],16) for i in (1,3,5)],dtype=np.float32)

def regular(file, maximum=512*1024*1024):
    p=Path(file)
    if p.is_symlink() or not p.is_file() or not 0<p.stat().st_size<=maximum:
        raise ValueError('Input must be a bounded, regular, non-symlink media file')
    return p

def image_palette(file):
    with Image.open(regular(file,64*1024*1024)) as im:
        im=im.convert('RGBA');im.thumbnail((96,96));a=np.asarray(im);colors=a[a[:,:,3]>128,:3]
    if not len(colors):return []
    bins,counts=np.unique((colors//16)*16,axis=0,return_counts=True)
    return ['#'+''.join(f'{int(c):02x}' for c in color) for color in bins[np.argsort(counts)[-128:]]]

def choose_key(palette):
    colors=np.array([rgb(c) for c in palette],dtype=np.float32) if palette else np.array([[128,128,128]],dtype=np.float32)
    # Farthest candidate from the known subject/style palette. Unknown future colours
    # are still a prompt/inspection constraint, never a mathematical guarantee.
    return max(CANDIDATES,key=lambda c:float(np.min(np.linalg.norm(colors-rgb(c),axis=1))))

def nearby_foreground(colors,trusted,radius=6):
    """Propagate trusted edge colours a bounded distance; never erode a mask."""
    values=colors.copy();known=trusted.copy();h,w=known.shape
    if not np.any(known):return values,known
    for _ in range(radius):
        total=np.zeros_like(values);count=np.zeros((h,w),dtype=np.float32)
        for dy,dx in ((-1,0),(1,0),(0,-1),(0,1)):
            target=(slice(max(0,dy),h+min(0,dy)),slice(max(0,dx),w+min(0,dx)))
            source=(slice(max(0,-dy),h+min(0,-dy)),slice(max(0,-dx),w+min(0,-dx)))
            valid=known[source];total[target]+=values[source]*valid[:,:,None];count[target]+=valid
        new=(~known)&(count>0)
        values[new]=total[new]/count[new,None];known|=new
    return values,known

def key_frame(frame,key,soft=False,tolerance=.06,softness=.20,despill=True):
    if not 0<=tolerance<.5 or not .001<=softness<=1:raise ValueError('Invalid matte thresholds')
    c=np.asarray(frame,dtype=np.float32)[:,:,:3]/255.;k=rgb(key)/255.
    if soft:
        # Chroma separation estimates neutral translucent material (smoke/steam).
        # Arbitrary coloured transparency cannot be uniquely reconstructed.
        kc=k-k.mean();cc=c-c.mean(axis=2,keepdims=True)
        alpha=np.clip(np.linalg.norm(cc-kc,axis=2)/max(float(np.linalg.norm(kc)),.01),0,1)
        alpha=np.where(alpha<tolerance,0,alpha)
    else:
        # Coverage is independent of despill. Learn small codec/background shifts
        # only from a predominantly uniform border matching the reserved hue.
        border=np.concatenate((c[0],c[-1],c[:,0],c[:,-1]))
        background=np.median(border,axis=0)
        direction=lambda v:v/np.maximum(np.max(v,axis=-1,keepdims=True),1/255.)
        if np.mean(np.linalg.norm(border-background,axis=1)<.04)>.8 and np.linalg.norm(direction(background)-direction(k))<.1:
            k=background
        distance=np.linalg.norm(c-k,axis=2)
        t=np.clip((distance-tolerance)/softness,0,1);alpha=t*t*(3-2*t)
        alpha=np.where(alpha<1/255.,0,alpha)
    unmix=(c-(1-alpha[:,:,None])*k)/np.maximum(alpha[:,:,None],1/255.)
    if not soft and despill and float(np.ptp(k))>.2:
        dominant=k>=(float(k.max())-float(np.ptp(k))*.25)
        if np.any(~dominant):
            excess=c[:,:,dominant].min(axis=2)-c[:,:,~dominant].max(axis=2)
            trusted=(alpha>.98)&(excess<=.02)
            foreground,near=nearby_foreground(c,trusted)
            contaminated=near&(~trusted)&(alpha>0)&(excess>.02)
            # Fit the mixing amount for COLOR recovery only. It must never
            # shrink coverage. No dark-outline or subject-palette assumption.
            delta=foreground-k
            amount=np.clip(np.sum((c-k)*delta,axis=2)/np.maximum(np.sum(delta*delta,axis=2),1e-5),0,1)
            recovered=(c-(1-amount[:,:,None])*k)/np.maximum(amount[:,:,None],1/255.)
            # Ill-conditioned mixtures amplify compression errors. Local clean
            # colour is a conservative fallback, not a global outline colour.
            residual=np.linalg.norm(c-(k+amount[:,:,None]*delta),axis=2)
            reliable=(amount>.15)&(residual<.04)
            replacement=np.where(reliable[:,:,None],recovered,foreground)
            unmix=np.where(contaminated[:,:,None],replacement,unmix)
    unmix=np.clip(unmix,0,1);unmix[alpha==0]=0
    return np.dstack((np.rint(unmix*255),np.rint(alpha*255))).astype(np.uint8)

def prepare_source(source,output,width,height,key,exact=False,input_key=None,pixel_art=False):
    regular(source,64*1024*1024)
    if Path(output).exists():raise FileExistsError(output)
    if not all(isinstance(x,int) and 32<=x<=1920 for x in (width,height)):raise ValueError('Invalid source dimensions')
    with Image.open(source) as raw:
        im=raw.convert('RGBA')
    alpha=np.asarray(im)[:,:,3]
    if exact and alpha.min()==255:
        if input_key is None:raise ValueError('Exact opaque artwork needs referenceBackground; supply transparent artwork or explicitly identify its existing flat background')
        im=Image.fromarray(key_frame(np.asarray(im),input_key))
    # Exact means no generative repainting; resizing/contain transform is recorded.
    original=im.size;scale=min(width/im.width,height/im.height)
    im=im.resize((max(1,round(im.width*scale)),max(1,round(im.height*scale))),Image.Resampling.NEAREST if pixel_art else Image.Resampling.LANCZOS)
    canvas=Image.new('RGB',(width,height),tuple(int(c) for c in rgb(key)))
    offset=((width-im.width)//2,(height-im.height)//2);canvas.paste(im,offset,im)
    canvas.save(output)
    return {'original_preserved':True,'input_size':list(original),'output_size':[width,height],'offset':list(offset),'fit':'contain','exact_artwork_not_regenerated':exact,'key':key}

def prepare_endpoint(source,output,width,height,key,source_anchor,target_anchor,pixel_art=False,expected_size=None):
    regular(source,64*1024*1024)
    if Path(output).exists():raise FileExistsError(output)
    if not all(type(v) is int and 32<=v<=1920 for v in (width,height)):raise ValueError('Invalid endpoint canvas')
    for anchor in (source_anchor,target_anchor):
        if not isinstance(anchor,list) or len(anchor)!=2 or not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in anchor):raise ValueError('Invalid endpoint anchor')
    with Image.open(source) as raw:
        if raw.format!='PNG' or 'A' not in raw.getbands():raise ValueError('Bridge endpoint must be an RGBA PNG')
        im=raw.convert('RGBA')
    if expected_size is not None and list(im.size)!=expected_size:raise ValueError('Endpoint PNG dimensions disagree with animation metadata')
    original=im.size;scale=min(width/im.width,height/im.height)
    size=(max(1,round(im.width*scale)),max(1,round(im.height*scale)))
    im=im.resize(size,Image.Resampling.NEAREST if pixel_art else Image.Resampling.LANCZOS)
    offset=[round(target_anchor[0]*width-source_anchor[0]*size[0]),round(target_anchor[1]*height-source_anchor[1]*size[1])]
    bounds=im.getchannel('A').getbbox()
    if not bounds:raise ValueError('Empty bridge endpoint')
    if bounds[0]+offset[0]<0 or bounds[1]+offset[1]<0 or bounds[2]+offset[0]>width or bounds[3]+offset[1]>height:raise ValueError('Anchor alignment would clip endpoint artwork; choose a compatible canvas/anchor')
    canvas=Image.new('RGB',(width,height),tuple(int(v) for v in rgb(key)));canvas.paste(im,tuple(offset),im);canvas.save(output)
    return {'input_size':list(original),'output_size':[width,height],'scale':scale,'offset':offset,'source_anchor':source_anchor,'target_anchor':target_anchor,'fit':'contain-anchor','original_preserved':True}

def validate_source(file,key):
    with Image.open(regular(file,64*1024*1024)) as im:a=np.asarray(im.convert('RGB'))
    distance=np.linalg.norm((a.astype(np.float32)-rgb(key))/255.,axis=2)
    border=np.concatenate((distance[0],distance[-1],distance[:,0],distance[:,-1]))
    coverage=float(np.mean(border<.22));foreground=float(np.mean(distance>.3))
    if coverage<.75 or foreground<.005:raise ValueError('Source has no usable uniform key background or no visible subject; inspect source before H3. No generation was submitted.')
    return {'border_key_fraction':coverage,'foreground_fraction':foreground,'key':key,'subject_colour_exclusion_verified':False}

def features(frames):
    out=[]
    for im in frames:
        a=np.asarray(im.resize((32,32),Image.Resampling.BOX),dtype=np.float32)/255.
        out.append(np.concatenate((a[:,:,:3]*a[:,:,3:4],a[:,:,3:4]),axis=2).reshape(-1))
    return np.array(out)

def select_loop(frames,fps,min_cycle_seconds=.25):
    f=features(frames);n=len(f)
    if n<4:return {'start':0,'end':n,'accepted':False,'reason':'Too few frames','pixel_perfect_guaranteed':False}
    deltas=np.mean(np.abs(np.diff(f,axis=0)),axis=1);motion=float(np.median(deltas))
    if float(np.max(np.ptp(f,axis=0)))<1/255:
        return {'start':0,'end':1,'accepted':True,'reason':'Static image; one-frame loop','score':0,'pixel_perfect_guaranteed':False}
    minimum=max(3,round(fps*min_cycle_seconds));best=None
    for start in range(0,n-minimum-2):
        for end in range(start+minimum,n-1):
            length=end-start
            # At least part of the next repetition must agree, not merely a
            # single coincidentally similar pose. End is exclusive in exports.
            look=min(length, max(2,round(fps*.25)),n-end-1)
            if look<2:continue
            appearance=float(np.mean(np.abs(f[start]-f[end])))
            continuation=float(np.mean(np.abs(f[start:start+look]-f[end:end+look])))
            velocity=float(np.mean(np.abs((f[start+1]-f[start])-(f[end+1]-f[end]))))
            score=appearance+continuation+velocity*.5
            candidate=(score,length,start,end,appearance,continuation,velocity)
            if best is None or candidate[:2]<best[:2]:best=candidate
    if best is None:return {'start':0,'end':n,'accepted':False,'reason':'No recurring interval','pixel_perfect_guaranteed':False}
    score,length,start,end,appearance,continuation,velocity=best
    relative=score/max(motion,.0001)
    return {'start':start,'end':end,'score':score,'relative_seam_error':relative,'appearance_error':appearance,'continuation_error':continuation,'velocity_error':velocity,'accepted':relative<.8,'reason':'Recurring appearance and motion seam evaluated; inspect the GIF','pixel_perfect_guaranteed':False}

def measure_loop_interval(frames,fps,start,end):
    """Measure the requested slice itself, not metrics from a different search result."""
    f=features(frames);length=end-start;look=min(length,max(2,round(fps*.25)),len(f)-end-1)
    result={'start':start,'end':end,'accepted':False,'reason':'Explicit interval has insufficient continuation to measure the seam','pixel_perfect_guaranteed':False}
    if length<2 or look<2:return result
    appearance=float(np.mean(np.abs(f[start]-f[end])))
    continuation=float(np.mean(np.abs(f[start:start+look]-f[end:end+look])))
    velocity=float(np.mean(np.abs((f[start+1]-f[start])-(f[end+1]-f[end]))))
    score=appearance+continuation+velocity*.5;motion=float(np.median(np.mean(np.abs(np.diff(f,axis=0)),axis=1)));relative=score/max(motion,.0001)
    return {**result,'accepted':relative<.8,'score':score,'relative_seam_error':relative,'appearance_error':appearance,'continuation_error':continuation,'velocity_error':velocity,'reason':'Explicit interval; actual appearance and motion seam measured'}

def checker(im):
    w,h=im.size;y,x=np.indices((h,w));v=np.where(((x//8+y//8)%2)==0,220,165).astype(np.uint8)
    bg=Image.fromarray(np.dstack((v,v,v,np.full_like(v,255))))
    return Image.alpha_composite(bg,im).convert('RGB')

def resize_sprite(im,size,pixel_art,antialias=False):
    crisp=im.resize(size,Image.Resampling.NEAREST if pixel_art else Image.Resampling.LANCZOS)
    if not antialias:return crisp
    # Supersample only the silhouette. Premultiplied filtering excludes hidden
    # RGB in transparent pixels; BOX avoids ringing or expanding the outline.
    sample=im if im.width>size[0] or im.height>size[1] else im.resize((size[0]*2,size[1]*2),Image.Resampling.NEAREST)
    smooth=np.asarray(sample.convert('RGBa').resize(size,Image.Resampling.BOX).convert('RGBA'))
    result=np.asarray(crisp).copy()
    edge=(smooth[:,:,3]>0)&(smooth[:,:,3]<255)
    result[edge]=smooth[edge]
    result[smooth[:,:,3]==0]=0
    return Image.fromarray(result)

def process_video(video,output,options):
    regular(video);out=Path(output)
    if out.exists():raise FileExistsError(output)
    fps=options.get('fps',12);width=options.get('width',256);height=options.get('height',256);key=options.get('key')
    rgb(key)
    if not all(isinstance(v,(float,int)) and math.isfinite(v) for v in (fps,width,height)) or not 1<=fps<=60 or not all(int(x)==x and 16<=x<=1024 for x in (width,height)):raise ValueError('Invalid frame rate or canvas')
    width,height=int(width),int(height)
    looping=options.get('loop',False)
    if type(looping) is not bool:raise ValueError('loop must be a resolved boolean')
    anchor=options.get('anchor',[.5,.5])
    if not isinstance(anchor,list) or len(anchor)!=2 or not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in anchor):raise ValueError('Invalid normalized anchor')
    probe=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height:format=duration','-of','json',str(Path(video).resolve())],check=True,capture_output=True,text=True,timeout=30)
    info=json.loads(probe.stdout);duration=float(info['format']['duration']);stream=info['streams'][0]
    if not math.isfinite(duration) or not 0<duration<=120 or duration*fps>900 or duration*fps*width*height>80_000_000 or stream['width']*stream['height']>8_000_000:raise ValueError('Video exceeds bounded processing budget')
    antialias=options.get('antialias',False)
    if type(antialias) is not bool:raise ValueError('antialias must be boolean')
    out.mkdir(mode=0o700,parents=True);(out/'frames').mkdir(mode=0o700)
    if type(options.get('despill',True)) is not bool:raise ValueError('despill must be boolean')
    report={'warnings':[],'matte':'estimated_soft' if options.get('soft') else 'coverage_separate_from_colour','pixel_perfect_guaranteed':False,'keying_resolution':{'width':stream['width'],'height':stream['height']},'resize_after_keying':True,'despill':'local_colour_reconstruction' if options.get('despill',True) and not options.get('soft') else 'off','pixel_art_alpha':'binary' if options.get('pixelArt') and not options.get('soft') else 'continuous'}
    report['antialias']='premultiplied_area_edges' if antialias else 'off'
    if antialias:report['pixel_art_alpha']='continuous'
    if options.get('despill',True) and not options.get('soft'):report['warnings'].append('Edge colours are locally estimated, not recovered ground truth. No motion-compensated temporal stabilization; inspect moving edges for colour variation.')
    if options.get('soft'):report['warnings'].append('Soft alpha is estimated from one colour background; coloured smoke/glass and occlusion require visual review.')
    with tempfile.TemporaryDirectory(prefix='sprite-frames-') as temp:
        # Decode RGB at native resolution. Scaling subsampled YUV first spreads
        # the coloured background into foreground pixels before it can be keyed.
        filt=f'fps={fps}:start_time=0:round=near,format=rgb24'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(Path(video).resolve()),'-an','-vf',filt,'-frames:v','901',str(Path(temp)/'%06d.png')],check=True,capture_output=True,timeout=180)
        frames=[];border_stats=[]
        for p in sorted(Path(temp).glob('*.png')):
            with Image.open(p) as im:a=np.asarray(im.convert('RGB'))
            rgba=key_frame(a,key,soft=options.get('soft',False),tolerance=options.get('tolerance',.06),softness=options.get('softness',.20),despill=options.get('despill',True))
            border=np.concatenate((rgba[0,:,3],rgba[-1,:,3],rgba[:,0,3],rgba[:,-1,3]));border_stats.append(float(np.mean(border<16)))
            im=Image.fromarray(rgba);scale=min(width/im.width,height/im.height)
            im=resize_sprite(im,(max(1,round(im.width*scale)),max(1,round(im.height*scale))),options.get('pixelArt',False),antialias)
            canvas=Image.new('RGBA',(width,height));canvas.paste(im,((width-im.width)//2,(height-im.height)//2))
            if options.get('pixelArt') and not options.get('soft') and not antialias:
                clean=np.asarray(canvas).copy();clean[:,:,3]=np.where(clean[:,:,3]>=128,255,0);clean[clean[:,:,3]==0,:3]=0;canvas=Image.fromarray(clean)
            frames.append(canvas)
    if not frames:raise ValueError('Video contains no decodable frames')
    report['input_frames']=len(frames);report['background_border_fraction_min']=min(border_stats)
    if min(border_stats)<.8:report['warnings'].append('Some frames have foreground/key contamination at the canvas edge; inspect clipping/background drift.')
    if max(float(np.mean(np.asarray(im)[:,:,3]<16)) for im in frames)<.01:report['warnings'].append('No substantial transparent background detected; selected key may be wrong.')
    loop=select_loop(frames,fps) if looping else {'start':0,'end':len(frames),'accepted':None,'reason':'One-shot requested','pixel_perfect_guaranteed':False}
    if looping and loop.get('end',0)-loop.get('start',0)==1:
        report['warnings'].append('Clip appears static at the analysis resolution/sample rate; one-frame loop is not evidence of successful motion.')
    start=options.get('startFrame',loop['start']);end=options.get('endFrame',loop['end'])
    if not all(type(x) is int for x in (start,end)) or not 0<=start<end<=len(frames):raise ValueError('Invalid extraction interval')
    if 'startFrame' in options or 'endFrame' in options:
        loop=measure_loop_interval(frames,fps,start,end) if looping else {'start':start,'end':end,'accepted':None,'reason':'Explicit one-shot interval','pixel_perfect_guaranteed':False}
    frames=frames[start:end]
    if looping and not loop['accepted']:report['warnings'].append('No low-error periodic seam found. Candidate loop exported for review, not labelled seamless.')
    final_duration=options.get('cycleSeconds',len(frames)/fps)
    if not isinstance(final_duration,(int,float)) or not math.isfinite(final_duration) or not 0<final_duration<=120:raise ValueError('Invalid final duration')
    frame_ms=final_duration*1000/len(frames)
    if frame_ms<10:raise ValueError('GIF preview requires at least 10 ms per frame')
    pad=2;columns=min(max(1,int(math.sqrt(len(frames)))),8192//(width+pad*2));rows=math.ceil(len(frames)/columns)
    if rows*(height+pad*2)>8192:raise ValueError('Sprite sheet exceeds 8192px texture limit; reduce output dimensions/FPS')
    sheet=Image.new('RGBA',(columns*(width+pad*2),rows*(height+pad*2)));records=[]
    for i,im in enumerate(frames):
        name=f'{i:04d}.png';im.save(out/'frames'/name)
        x=(i%columns)*(width+pad*2)+pad;y=(i//columns)*(height+pad*2)+pad;sheet.paste(im,(x,y))
        records.append({'file':'frames/'+name,'rect':{'x':x,'y':y,'w':width,'h':height},'duration_ms':frame_ms,'source_time_seconds':(start+i)/fps})
    sheet.save(out/'spritesheet.png')
    previews=[checker(im) for im in frames]
    # One shared palette prevents gratuitous palette flicker in the GIF.
    pilot=Image.new('RGB',(32,32*min(64,len(previews))))
    for j,index in enumerate(np.linspace(0,len(previews)-1,min(64,len(previews)),dtype=int)):pilot.paste(previews[index].resize((32,32)),(0,32*j))
    palette=pilot.quantize(colors=256);gifs=[im.quantize(palette=palette,dither=Image.Dither.NONE) for im in previews]
    delays=[max(10,round((i+1)*frame_ms/10)*10-round(i*frame_ms/10)*10) for i in range(len(frames))]
    gifs[0].save(out/'preview.gif',save_all=True,append_images=gifs[1:],duration=delays,disposal=2,optimize=False,**({'loop':0} if looping else {}))
    count=min(12,len(frames));contact=Image.new('RGB',(min(4,count)*width,math.ceil(count/4)*height),(32,32,32))
    for j,index in enumerate(np.linspace(0,len(frames)-1,count,dtype=int)):contact.paste(previews[index],((j%4)*width,(j//4)*height))
    contact.save(out/'contact-sheet.png')
    report['loop']=loop;report['status']='needs_review' if report['warnings'] else 'processed'
    metadata={'version':1,'canvas':{'width':width,'height':height},'anchor':anchor,'loop':looping,'fps':len(frames)/final_duration,'duration_ms':final_duration*1000,'source_interval':{'start_frame':start,'end_frame_exclusive':end,'sample_fps':fps},'frames':records,'spritesheet':'spritesheet.png','preview':'preview.gif','key':key,'alpha':'straight RGBA','quality_report':'quality-report.json','source_sha256':hashlib.sha256(Path(video).read_bytes()).hexdigest()}
    (out/'animation.json').write_text(json.dumps(metadata,indent=2)+'\n');(out/'quality-report.json').write_text(json.dumps(report,indent=2)+'\n')
    for p in out.rglob('*'):
        if p.is_file():p.chmod(0o600)
    return {'output':str(out),'frames':len(frames),'duration_ms':final_duration*1000,'quality':report}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['check','plan','prepare','endpoint','validate','process']);args=parser.parse_args();data=json.load(sys.stdin)
    if args.action=='check':
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise ValueError('Install FFmpeg/ffprobe before starting; no generation attempted')
        result={'ready':True,'pillow':Image.__version__,'numpy':np.__version__}
    elif args.action=='plan':
        palette=data.get('palette',[])
        if data.get('reference'):palette+=image_palette(data['reference'])
        for reference in data.get('references',[]):palette+=image_palette(reference)
        result={'key':choose_key(palette),'palette':palette,'guaranteed_absent':False}
    elif args.action=='prepare':result=prepare_source(**data)
    elif args.action=='endpoint':result=prepare_endpoint(**data)
    elif args.action=='validate':result=validate_source(**data)
    else:result=process_video(**data)
    print(json.dumps(result))

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);sys.exit(1)
