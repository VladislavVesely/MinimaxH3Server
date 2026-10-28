"""Local, quota-free sprite preparation/extraction. No inference or credentials.

Soft alpha from one chroma background is an estimate, not recovered ground truth.
All exports use a shared canvas; no per-frame recentering, mirroring or forced seam.
"""
import argparse
import base64
import io
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

def choose_key(palette,native=False):
    colors=np.array([rgb(c) for c in palette],dtype=np.float32) if palette else np.array([[128,128,128]],dtype=np.float32)
    # Farthest candidate from the known subject/style palette. Unknown future colours
    # are still a prompt/inspection constraint, never a mathematical guarantee.
    candidates=CANDIDATES
    if native:
        candidates=[c for c in candidates if (lambda v:v[2]-v[1]>=31 or v[2]-v[0]<31)(sorted(rgb(c)))]
    return max(candidates,key=lambda c:float(np.min(np.linalg.norm(colors-rgb(c),axis=1))))

def despill_radius(height,width):
    # Reconstruction runs BEFORE resizing. Six pixels at the 128px reference
    # scale must cover 24 native pixels at 512px, not six native pixels.
    # Bound work for extreme/aspect-ratio inputs; this remains a local estimate.
    return min(64,max(6,math.ceil(max(height,width)*6/128)))

def nearby_foreground(colors,trusted,radius=6,blend=True):
    """Propagate trusted edge colours a bounded distance; never erode a mask."""
    values=colors.copy();known=trusted.copy();h,w=known.shape
    if not np.any(known):return values,known
    for _ in range(radius):
        total=np.zeros_like(values);count=np.zeros((h,w),dtype=np.float32)
        for dy,dx in ((-1,0),(1,0),(0,-1),(0,1)):
            target=(slice(max(0,dy),h+min(0,dy)),slice(max(0,dx),w+min(0,dx)))
            source=(slice(max(0,-dy),h+min(0,-dy)),slice(max(0,-dx),w+min(0,-dx)))
            valid=known[source]
            if not blend:valid=valid&(count[target]==0)
            total[target]+=values[source]*valid[:,:,None];count[target]+=valid
        new=(~known)&(count>0)
        values[new]=total[new]/count[new,None];known|=new
    return values,known

def border_key(frame):
    a=np.asarray(frame,dtype=np.float32)[:,:,:3]
    border=np.concatenate((a[0],a[-1],a[:,0],a[:,-1]))
    key=np.median(border,axis=0)
    if np.mean(np.linalg.norm(border-key,axis=1)<16)<.98:
        raise ValueError('Nonuniform border: manual review required')
    return key

def despill_matte(frame,key,alpha,palette=()):
    """Reconstruct only plausible local key mixtures; NEVER change supplied coverage.

    Geometric interior seeds avoid the hue-only trust failure on orange/red keys
    matching fur. Interior colours and non-mixtures remain byte-identical. This
    is bounded colour estimation, not recovery of unknown original paint.
    """
    c=np.asarray(frame,dtype=np.float32)[:,:,:3]/255.;k=np.asarray(key,dtype=np.float32)/255.
    alpha=np.asarray(alpha)
    if alpha.dtype!=np.uint8 or alpha.shape!=c.shape[:2] or k.shape!=(3,) or not np.isfinite(k).all():
        raise ValueError('Expected native RGB key and matching uint8 alpha')
    mask=alpha>0;interior=mask.copy()
    inset=max(2,min(32,math.ceil(max(mask.shape)*4/128)))
    for _ in range(inset):
        p=np.pad(interior,1);interior=p[1:-1,1:-1]&p[:-2,1:-1]&p[2:,1:-1]&p[1:-1,:-2]&p[1:-1,2:]
    # Retained enclosed key residues must not become trusted colour seeds.
    dominant=k>=(float(k.max())-float(np.ptp(k))*.25)
    excess=c[:,:,dominant].min(axis=2)-c[:,:,~dominant].max(axis=2) if np.any(~dominant) else np.zeros(mask.shape)
    core=interior&(np.linalg.norm(c-k,axis=2)>.25)
    explicit=np.array([rgb(v)/255 for v in palette],dtype=float).reshape(-1,3)
    exclusive_axis=False;limit=None;exclusive_axes=[]
    if len(explicit) and np.any(~dominant):
        paint_excess=explicit[:,dominant].min(axis=1)-explicit[:,~dominant].max(axis=1)
        # A key-only chromatic axis (e.g. green absent from the artwork) must
        # not acquire a broad 'close enough to palette' exemption. Warm fur or
        # explicitly declared key-like paint retains the wider collision guard.
        exclusive_axis=float(paint_excess.max())<=.02
        limit=max(.02,float(paint_excess.max())+.04) if exclusive_axis else max(.10,float(paint_excess.max())+.18)
        key_excess=float(k[dominant].min()-k[~dominant].max())
        if key_excess>limit:
            # Enclosed codec-tinted key pockets may be inside the alpha mask.
            # They cannot seed an invented neon foreground colour absent from
            # the declared paint palette. Real key-like paint disables this.
            core&=excess<=limit
        if exclusive_axis:exclusive_axes.append((dominant,limit))
        # Green remains an absent paint axis even when a drifting key becomes
        # cyan and the primary classifier groups G+B. Chroma subsampling can
        # leave a green-only fringe that the combined cyan test calls neutral.
        for channel in range(3):
            axis=np.arange(3)==channel
            paint_axis=explicit[:,axis].min(axis=1)-explicit[:,~axis].max(axis=1)
            if float(paint_axis.max())<=.02 and dominant[channel] and float(np.ptp(k))>.2:
                exclusive_axes.append((axis,max(.02,float(paint_axis.max())+.04)))
    def violates_axis(values):
        bad=np.zeros(values.shape[:-1],dtype=bool)
        for axis,cap in exclusive_axes:bad|=(values[...,axis].min(axis=-1)-values[...,~axis].max(axis=-1))>cap
        return bad
    invalid_paint=violates_axis(c)
    core&=~invalid_paint
    trusted=(core|(mask&(excess<=.02)))&(~invalid_paint)
    foreground,near=nearby_foreground(c,trusted,despill_radius(*mask.shape),blend=False)
    delta=foreground-k;den=np.sum(delta*delta,axis=2)
    amount=np.clip(np.sum((c-k)*delta,axis=2)/np.maximum(den,1e-5),0,1)
    residual=np.linalg.norm(c-(k+amount[:,:,None]*delta),axis=2)
    candidates=mask&near&(~trusted)&((excess>.02)|invalid_paint)
    # Subsampled chroma need not lie on an exact RGB mixing line. A bounded
    # fallback is safe only away from genuine foreground palette colours. Key
    # / palette collisions disable it except for unmistakably key-like residue.
    bins,counts=np.unique(np.rint(c[core]*15).astype(np.uint8),axis=0,return_counts=True)
    learned=bins[np.argsort(counts)[-128:]].astype(float)/15 if len(bins) else np.empty((0,3))
    colors=np.concatenate((learned,explicit))
    fallback=np.zeros(mask.shape,dtype=bool);protected=np.zeros(mask.shape,dtype=bool)
    if len(colors) and np.any(candidates):
        points=c[candidates];distance=np.full(len(points),np.inf)
        for color in colors:distance=np.minimum(distance,np.linalg.norm(points-color,axis=1))
        axis_violation=invalid_paint[candidates]
        protected[candidates]=(distance<=.15)&(~axis_violation)
        # Collision belongs to this pixel's nearby paint, not ANY colour
        # anywhere in the subject palette (a teal gun cannot protect a green
        # halo around a brown head).
        collision=np.linalg.norm(foreground[candidates]-k,axis=1)<.5
        fallback[candidates]=((distance>.15)|axis_violation)&((~collision)|(np.linalg.norm(points-k,axis=1)<.35)|axis_violation)
    fitted=mask&(~interior)&near&(~protected)&(den>.02)&(amount<.96)&(residual<.045)
    contaminated=fitted|fallback
    # Ill-conditioned edge mixtures use nearby clean paint rather than amplify
    # codec noise. Mask/geometry are independent of the fitted colour amount.
    recovered=(c-(1-amount[:,:,None])*k)/np.maximum(amount[:,:,None],1/255.)
    replacement=np.where(((amount>.2)&(residual<.045))[:,:,None],recovered,foreground)
    if exclusive_axes:
        # Small fit residuals can become large chroma errors after division by
        # a small mixture amount. Never reintroduce an absent key-only hue.
        replacement=np.where(violates_axis(replacement)[:,:,None],foreground,replacement)
    result=np.asarray(frame,dtype=np.uint8)[:,:,:3].copy()
    result[contaminated]=np.rint(np.clip(replacement[contaminated],0,1)*255).astype(np.uint8)
    return np.dstack((result,alpha))

def recovered_key_frame(frame,threshold=40,despill=True,palette=()):
    """Explicit uniform-border recovery; enclosed colours are NOT globally keyed."""
    if not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or not 0<threshold<=80:
        raise ValueError('Invalid reviewed backgroundThreshold')
    a=np.asarray(frame,dtype=np.uint8)[:,:,:3];key=border_key(a)
    candidate=np.linalg.norm(a.astype(float)-key,axis=2)<=threshold
    # Flood-fill using Pillow's compiled implementation, no SciPy dependency.
    from PIL import ImageDraw
    barrier=np.pad(~candidate,1,constant_values=False).astype(np.uint8)*255
    flood=Image.fromarray(barrier).copy();ImageDraw.floodfill(flood,(0,0),128)
    background=np.asarray(flood)[1:-1,1:-1]==128
    alpha=np.where(background,0,255).astype(np.uint8)
    return despill_matte(a,key,alpha,palette) if despill else np.dstack((a,alpha))

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
            foreground,near=nearby_foreground(c,trusted,despill_radius(*c.shape[:2]))
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
        # Supplied artwork is not codec-contaminated video. Donor-based despill
        # can mistake intentional ink/highlights for the old key and repaint
        # them before inference. Only unmix the identified background here;
        # fully opaque foreground RGB must remain the supplied paint.
        im=Image.fromarray(key_frame(np.asarray(im),input_key,despill=False))
    # Exact means no generative repainting; resizing/contain transform is recorded.
    original=im.size;scale=min(width/im.width,height/im.height)
    im=im.resize((max(1,round(im.width*scale)),max(1,round(im.height*scale))),Image.Resampling.NEAREST if pixel_art else Image.Resampling.LANCZOS)
    canvas=Image.new('RGB',(width,height),tuple(int(c) for c in rgb(key)))
    offset=((width-im.width)//2,(height-im.height)//2);canvas.paste(im,offset,im)
    canvas.save(output)
    return {'original_preserved':True,'input_size':list(original),'output_size':[width,height],'offset':list(offset),'fit':'contain','exact_artwork_not_regenerated':exact,'key':key}

def reference_preview(frames):
    if not isinstance(frames,list) or not 1<=len(frames)<=12:raise ValueError('Inspect 1–12 reference frames at a time')
    sheet=Image.new('RGB',(min(4,len(frames))*192,math.ceil(len(frames)/4)*216),'#252525');draw=ImageDraw.Draw(sheet)
    for i,item in enumerate(frames):
        regular(item['file'],64*1024*1024)
        with Image.open(item['file']) as raw:
            if raw.format!='PNG' or 'A' not in raw.getbands():raise ValueError('Reference frame must be an RGBA PNG')
            im=raw.convert('RGBA');im.thumbnail((192,192),Image.Resampling.NEAREST)
        x=(i%4)*192;y=(i//4)*216
        sheet.paste(checker(im),(x+(192-im.width)//2,y+(192-im.height)//2));draw.text((x+4,y+196),'Frame '+str(item['frameIndex']),fill='white')
    buffer=io.BytesIO();sheet.save(buffer,format='PNG')
    return {'mimeType':'image/png','data':base64.b64encode(buffer.getvalue()).decode('ascii')}

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

def unpack_server_rgba(archive,digest,source,fps,key,destination,expected_job=None):
    import zipfile
    native=expected_job is not None
    data=regular(archive,(256 if native else 128)*1024**2).read_bytes()
    if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Server RGBA archive checksum mismatch')
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist()
        if len(infos)>901 or sum(i.file_size for i in infos)>256*1024**2 or any(i.file_size>16*1024**2 for i in infos):raise ValueError('Server RGBA archive exceeds budget')
        if z.getinfo('manifest.json').file_size>65536:raise ValueError('Invalid server RGBA manifest')
        meta=json.loads(z.read('manifest.json'));n=meta.get('frames');w=meta.get('width');h=meta.get('height')
        if native:
            valid=bool(re.fullmatch('[a-f0-9]{32}',str(expected_job))) and meta.get('processor')=='satodive-direct-vae-v1' and meta.get('frame_source')=='vae-decoded-float32' and meta.get('frame_selection')=='all' and meta.get('job_id')==expected_job and meta.get('preview_video_sha256')==source and meta.get('source_sha256')==meta.get('raw_frames_sha256') and bool(re.fullmatch('[a-f0-9]{64}',str(meta.get('raw_frames_sha256','')))) and bool(re.fullmatch('[a-f0-9]{64}',str(meta.get('generation_settings_sha256','')))) and meta.get('fps')==meta.get('source_fps')==24
        else:valid=meta.get('processor')=='satodive-cpu-v1' and meta.get('source_sha256')==source and meta.get('fps')==fps
        if meta.get('version')!=1 or not valid or meta.get('key')!=key.lower() or meta.get('alpha')!='straight RGBA':raise ValueError('Server RGBA provenance mismatch')
        if not all(type(x) is int and x>0 for x in (n,w,h)) or n>900 or max(w,h)>1920 or n*w*h>(json.loads((Path(__file__).resolve().parent/'sprite-defaults.json').read_text())['limits']['nativeImportPixels'] if native else 40_000_000):raise ValueError('Server RGBA geometry budget')
        names=['manifest.json']+[f'frames/{i:04d}.png' for i in range(n)]
        if len(infos)!=len(names) or sorted(z.namelist())!=sorted(names):raise ValueError('Unexpected server RGBA archive members')
        for i in range(n):
            with z.open(f'frames/{i:04d}.png') as f,Image.open(f) as im:
                if im.size!=(w,h) or im.mode!='RGBA' or getattr(im,'n_frames',1)!=1:raise ValueError('Invalid server RGBA frame')
                im.load();im.save(Path(destination)/f'{i:06d}.png')
    return meta

def process_video(video,output,options,rgba_archive=None,rgba_sha256=None,rgba_job_id=None):
    regular(video);out=Path(output)
    if out.exists():raise FileExistsError(output)
    fps=options.get('fps',12);output_fps=fps;width=options.get('width',256);height=options.get('height',256);key=options.get('key')
    rgb(key)
    if not all(isinstance(v,(float,int)) and math.isfinite(v) for v in (fps,width,height)) or not 1<=fps<=60 or not all(int(x)==x and 16<=x<=1024 for x in (width,height)):raise ValueError('Invalid frame rate or canvas')
    width,height=int(width),int(height)
    background_mode=options.get('backgroundMode','fixed')
    if background_mode not in ('fixed','reviewed-border'):raise ValueError('Invalid backgroundMode')
    if background_mode=='reviewed-border' and options.get('soft'):raise ValueError('Reviewed border recovery requires a solid matte')
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
    report['despill_radius_pixels']=despill_radius(stream['height'],stream['width']) if report['despill']!='off' else 0
    report['background_mode']=background_mode
    if background_mode=='reviewed-border':
        report['despill']='matte_preserving_local_mixture' if options.get('despill',True) else 'off'
        report['warnings'].append('Explicit per-frame border recovery: colour collisions and enclosed key residue require visual review; no uniform-key or perfect-colour guarantee.')
    report['antialias']='premultiplied_area_edges' if antialias else 'off'
    if antialias:report['pixel_art_alpha']='continuous'
    if options.get('despill',True) and not options.get('soft'):report['warnings'].append('Edge colours are locally estimated, not recovered ground truth. No motion-compensated temporal stabilization; inspect moving edges for colour variation.')
    if options.get('soft'):report['warnings'].append('Soft alpha is estimated from one colour background; coloured smoke/glass and occlusion require visual review.')
    with tempfile.TemporaryDirectory(prefix='sprite-frames-') as temp:
        # Decode RGB at native resolution. Scaling subsampled YUV first spreads
        # the coloured background into foreground pixels before it can be keyed.
        filt=f'fps={fps}:start_time=0:round=near,format=rgb24'
        if rgba_archive:
            meta=unpack_server_rgba(rgba_archive,rgba_sha256,hashlib.sha256(Path(video).read_bytes()).hexdigest(),fps,key,Path(temp),expected_job=rgba_job_id)
            if rgba_job_id:fps=meta['fps']  # All native frames first; requested output FPS is applied only after selection.
            if (meta['width'],meta['height'])!=(stream['width'],stream['height']):raise ValueError('Server RGBA dimensions differ from original')
            report.update(matte='experimental_server_estimate',despill='satodive_direct_vae_v1' if rgba_job_id else 'satodive_cpu_v1',despill_radius_pixels=None,server_transparency=meta,background_mode='server-estimate',pixel_art_alpha='continuous' if antialias else 'server-provided')
            report['warnings']=meta.get('warnings',[])+meta.get('analysis',{}).get('warnings',[])+['Experimental server keying; inspect coverage, fine parts, colour and temporal edges against the baseline.']
        else:
            subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(Path(video).resolve()),'-an','-vf',filt,'-frames:v','901',str(Path(temp)/'%06d.png')],check=True,capture_output=True,timeout=180)
        frames=[];border_stats=[]
        for p in sorted(Path(temp).glob('*.png')):
            with Image.open(p) as im:a=np.asarray(im.convert('RGBA' if rgba_archive else 'RGB'))
            if rgba_archive:
                rgba=a
            elif background_mode=='reviewed-border':
                rgba=recovered_key_frame(a,options.get('backgroundThreshold',40),options.get('despill',True),options.get('protectedPalette',()))
            else:
                if not options.get('soft'):
                    actual=border_key(a)
                    direction=lambda v:v/max(float(np.max(v)),1.)
                    if np.linalg.norm(direction(actual)-direction(rgb(key)))>.15:
                        raise ValueError('Native background hue drift: inspect original, then explicitly opt into backgroundMode=reviewed-border; no inference retry needed')
                rgba=key_frame(a,key,soft=options.get('soft',False),tolerance=options.get('tolerance',.06),softness=options.get('softness',.20),despill=options.get('despill',True))
            border=np.concatenate((rgba[0,:,3],rgba[-1,:,3],rgba[:,0,3],rgba[:,-1,3]));border_stats.append(float(np.mean(border<16)))
            im=Image.fromarray(rgba);scale=min(width/im.width,height/im.height)
            im=resize_sprite(im,(max(1,round(im.width*scale)),max(1,round(im.height*scale))),options.get('pixelArt',False),antialias)
            canvas=Image.new('RGBA',(width,height));canvas.paste(im,((width-im.width)//2,(height-im.height)//2))
            # Server PNGs are already keyed straight RGBA. Pixel-art sampling
            # must not reclassify their coverage or promote partial alpha to 255.
            if not rgba_archive and options.get('pixelArt') and not options.get('soft') and not antialias:
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
    # Retiming changes playback duration, not the supported output rate. Sample
    # across the WHOLE selected interval; never truncate it to hit a duration.
    source_indices=list(range(start,end))
    target_count=max(1,int(math.floor(final_duration*output_fps+1e-9)))
    retain=options.get('retainSelectedFrames',False)
    if type(retain) is not bool:raise ValueError('retainSelectedFrames must be boolean')
    if retain:
        report['retiming']={'method':'all-selected-poses-duration-only','output_frames':len(frames),'source_fps':fps,'no_pose_dropped':True}
    if not retain and len(frames)>target_count:
        if not looping and target_count<2:
            raise ValueError('One-shot duration too short to retain both endpoints at requested FPS')
        indices=(np.floor(np.arange(target_count)*len(frames)/target_count).astype(int)
                 if looping else np.rint(np.linspace(0,len(frames)-1,target_count)).astype(int))
        frames=[frames[int(i)] for i in indices]
        source_indices=[source_indices[int(i)] for i in indices]
        report['retiming']={'method':'nearest-frame-whole-interval','output_frames':len(frames),'requested_fps':output_fps}
        report['warnings'].append('Retimed interval resampled without interpolation; inspect motion and joins at gameplay speed.')
    frame_ms=final_duration*1000/len(frames)
    if frame_ms<1000/60:raise ValueError('Final duration exceeds supported 60 FPS playback rate')
    pad=2;columns=min(max(1,int(math.sqrt(len(frames)))),8192//(width+pad*2));rows=math.ceil(len(frames)/columns)
    if rows*(height+pad*2)>8192:raise ValueError('Sprite sheet exceeds 8192px texture limit; reduce output dimensions/FPS')
    sheet=Image.new('RGBA',(columns*(width+pad*2),rows*(height+pad*2)));records=[]
    for i,im in enumerate(frames):
        name=f'{i:04d}.png';im.save(out/'frames'/name)
        x=(i%columns)*(width+pad*2)+pad;y=(i//columns)*(height+pad*2)+pad;sheet.paste(im,(x,y))
        records.append({'file':'frames/'+name,'rect':{'x':x,'y':y,'w':width,'h':height},'duration_ms':frame_ms,'source_time_seconds':source_indices[i]/fps})
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
    if rgba_archive:
        # Lossless animated delivery with real RGBA, unlike the checkerboard GIF.
        # APNG stores millisecond delays; diffuse rounding rather than change speed.
        png_delays=[round((i+1)*frame_ms)-round(i*frame_ms) for i in range(len(frames))]
        frames[0].save(out/'animation.apng',format='PNG',save_all=True,append_images=frames[1:],duration=png_delays,loop=0 if looping else 1,disposal=0,blend=0)
        metadata['transparent_animation']='animation.apng'
        metadata['server_transparency']=report['server_transparency']
    (out/'animation.json').write_text(json.dumps(metadata,indent=2)+'\n');(out/'quality-report.json').write_text(json.dumps(report,indent=2)+'\n')
    for p in out.rglob('*'):
        if p.is_file():p.chmod(0o600)
    return {'output':str(out),'frames':len(frames),'duration_ms':final_duration*1000,'quality':report}

def reprocess_export(video,base,output,options):
    """Quota-free native colour repair retaining a pinned export's EXACT matte/poses.

    Explicit preserve mode is intentionally separate from re-keying or re-timing.
    Existing gap repairs, offsets, intervals and wide canvases must survive.
    """
    video=regular(video);base=Path(base);out=Path(output)
    if options.get('backgroundMode','fixed') not in ('fixed','reviewed-border'):raise ValueError('Invalid backgroundMode')
    if out.exists():raise FileExistsError(output)
    original_bytes=regular(base/'animation.json',4*1024*1024).read_bytes();meta=json.loads(original_bytes)
    digest=hashlib.sha256(video.read_bytes()).hexdigest()
    if meta.get('source_sha256')!=digest:raise ValueError('Preserved export source video hash differs')
    frames=meta.get('frames',[]);width=meta['canvas']['width'];height=meta['canvas']['height']
    if not 1<=len(frames)<=900 or not all(type(v) is int and 16<=v<=1024 for v in (width,height)) or len(frames)*width*height>80_000_000:
        raise ValueError('Preserved export exceeds processing budget')
    for f in frames:
        if not re.fullmatch(r'frames/\d{4,6}\.png',f['file']) or not isinstance(f.get('source_time_seconds'),(float,int)) or not math.isfinite(f['source_time_seconds']) or f['source_time_seconds']<0 or not f['duration_ms']>0:
            raise ValueError('Invalid preserved frame record')
    probe=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height,avg_frame_rate:format=duration','-of','json',str(video.resolve())],check=True,capture_output=True,text=True,timeout=30)
    info=json.loads(probe.stdout);stream=info['streams'][0];nw,nh=stream['width'],stream['height']
    if nw*height!=nh*width:raise ValueError('Preserved canvas has an unrecorded source transform; refusing to stretch artwork')
    num,den=map(int,stream['avg_frame_rate'].split('/'));native_fps=num/den
    if not 1<=native_fps<=120 or nw*nh>8_000_000 or float(info['format']['duration'])>120:raise ValueError('Invalid bounded source video')
    indices=[round(f['source_time_seconds']*native_fps) for f in frames];unique=sorted(set(indices))
    if len(unique)*nw*nh>500_000_000:raise ValueError('Native colour repair exceeds decoded pixel budget')
    images=[];records=[]
    with tempfile.TemporaryDirectory(prefix='sprite-native-colour-') as temp:
        select="select='"+'+'.join(f'eq(n,{i})' for i in unique)+"',format=rgb24"
        subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(video.resolve()),'-an','-vf',select,'-vsync','0',str(Path(temp)/'%06d.png')],check=True,capture_output=True,timeout=180)
        files=sorted(Path(temp).glob('*.png'))
        if len(files)!=len(unique):raise ValueError('Preserved source time outside video')
        decoded=dict(zip(unique,files))
        for f,index in zip(frames,indices):
            file=regular(base/f['file'],64*1024*1024);old=Image.open(file).convert('RGBA')
            if old.size!=(width,height):raise ValueError('Preserved PNG dimensions disagree with metadata')
            raw=np.asarray(Image.open(decoded[index]).convert('RGB'));key=border_key(raw)
            if options.get('backgroundMode','fixed')!='reviewed-border':
                direction=lambda v:v/max(float(np.max(v)),1.)
                if np.linalg.norm(direction(key)-direction(rgb(meta['key'])))>.15:
                    raise ValueError('Native background hue drift requires explicit backgroundMode=reviewed-border')
            alpha=np.asarray(old.getchannel('A').resize((nw,nh),Image.Resampling.NEAREST))
            rgba=despill_matte(raw,key,alpha,options.get('protectedPalette',()))
            clean=np.array(Image.fromarray(rgba).resize((width,height),Image.Resampling.NEAREST));prior=np.asarray(old)
            clean[:,:,3]=prior[:,:,3];clean[prior[:,:,3]==0,:3]=prior[prior[:,:,3]==0,:3]
            images.append(Image.fromarray(clean));records.append({'file':f['file'],'base_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'native_frame':index,'native_key':key.tolist(),'rgb_changed_opaque_pixels':int(np.sum(np.any(clean[:,:,:3]!=prior[:,:,:3],axis=2)&(prior[:,:,3]>0)))})
    if not re.fullmatch(r'[\w-]+\.png',meta['spritesheet']):raise ValueError('Invalid preserved atlas path')
    sheet=Image.open(regular(base/meta['spritesheet'])).convert('RGBA')
    for f in frames:
        r=f['rect']
        if r['w']!=width or r['h']!=height or min(r['x'],r['y'])<0 or r['x']+width>sheet.width or r['y']+height>sheet.height:raise ValueError('Preserved atlas rect outside canvas')
    out.mkdir(mode=0o700,parents=True);(out/'frames').mkdir(mode=0o700)
    for f,im in zip(frames,images):im.save(out/f['file']);sheet.paste(im,(f['rect']['x'],f['rect']['y']))
    sheet.save(out/meta['spritesheet'])
    previews=[checker(im) for im in images];pilot=Image.new('RGB',(32,32*min(64,len(previews))))
    for j,index in enumerate(np.linspace(0,len(previews)-1,min(64,len(previews)),dtype=int)):pilot.paste(previews[index].resize((32,32)),(0,j*32))
    palette=pilot.quantize(colors=256);gifs=[im.quantize(palette=palette,dither=Image.Dither.NONE) for im in previews];elapsed=0;delays=[]
    for f in frames:
        end=elapsed+f['duration_ms'];delays.append(max(10,round(end/10)*10-round(elapsed/10)*10));elapsed=end
    gifs[0].save(out/'preview.gif',save_all=True,append_images=gifs[1:],duration=delays,disposal=2,optimize=False,**({'loop':0} if meta['loop'] else {}))
    count=min(12,len(images));contact=Image.new('RGB',(min(count,4)*width,math.ceil(count/4)*height))
    for j,index in enumerate(np.linspace(0,len(images)-1,count,dtype=int)):contact.paste(previews[index],(j%4*width,j//4*height))
    contact.save(out/'contact-sheet.png')
    repair={'version':1,'method':'native matte-preserving local mixture reconstruction with palette/collision guards','processor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'base_export':base.name,'base_metadata_sha256':hashlib.sha256(original_bytes).hexdigest(),'source_video_sha256':digest,'alpha_unchanged':True,'sampling_timing_anchor_unchanged':True,'inherited_recovery_describes_matte_only':True,'protected_palette':options.get('protectedPalette',[]),'frames':records}
    meta['color_repair']=repair
    report={'status':'needs_review','despill':'matte_preserving_local_mixture','alpha_unchanged':True,'color_repair':repair,'warnings':['Colour estimates are not recovered original paint. Inspect silhouettes, legitimate palette colours, effects and temporal playback. Existing alpha and motion defects are not repaired.'],'pixel_perfect_guaranteed':False}
    (out/'animation.json').write_text(json.dumps(meta,indent=2)+'\n');(out/'quality-report.json').write_text(json.dumps(report,indent=2)+'\n')
    return {'output':str(out),'frames':len(frames),'duration_ms':meta['duration_ms'],'quality':report}

def bridge_image(record):
    file=regular(record['file'],64*1024*1024);data=file.read_bytes()
    if hashlib.sha256(data).hexdigest()!=record['sha256']:raise ValueError('Bridge frame hash changed')
    return Image.open(io.BytesIO(data)).convert('RGBA')

def bridge_analyze(output,from_context,to_context,target_seconds=.5):
    folder=Path(output);meta=json.loads(regular(folder/'animation.json').read_text());frames=meta['frames']
    if not 2<=len(frames)<=900 or not 0<float(target_seconds)<=120:raise ValueError('Invalid bridge analysis interval')
    def feature(im):
        a=np.asarray(im.resize((32,32),Image.Resampling.NEAREST),dtype=np.float32)/255.;a[:,:,:3]*=a[:,:,3:4];return a
    values=np.stack([feature(Image.open(regular(folder/f['file'])).convert('RGBA')) for f in frames]);a=[feature(bridge_image(f)) for f in from_context];b=[feature(bridge_image(f)) for f in to_context]
    if not a or not b:raise ValueError('Bridge needs actual neighbor contexts')
    fps=1000/float(frames[0]['duration_ms']);va=(a[-1]-a[-2])/(from_context[-2]['duration_ms']/1000) if len(a)>1 else None;vb=(b[1]-b[0])/(to_context[0]['duration_ms']/1000) if len(b)>1 else None
    target=max(2,round(float(target_seconds)*fps));minimum=max(2,round(target*.75));maximum=min(len(frames),max(minimum,round(target*1.5)));candidates=[]
    for size in range(minimum,maximum+1):
        for start in range(len(frames)-size+1):
            end=start+size;pose=float(np.mean(np.abs(values[start]-a[-1]))+np.mean(np.abs(values[end-1]-b[0])));velocity=0.
            if va is not None:velocity+=float(np.mean(np.abs((values[start+1]-values[start])*fps-va)))
            if vb is not None:velocity+=float(np.mean(np.abs((values[end-1]-values[end-2])*fps-vb)))
            duration=sum(f['duration_ms'] for f in frames[start:end]);score=pose+.05*velocity+.15*abs(duration/1000-float(target_seconds))/float(target_seconds)
            candidates.append({'startFrame':start,'endFrame':end,'duration_ms':duration,'boundaryPoseScore':pose,'boundaryVelocityScore':velocity,'score':score})
    chosen=[]
    for c in sorted(candidates,key=lambda x:x['score']):
        if all(abs(c['startFrame']-x['startFrame'])>=max(2,target//2) for x in chosen):chosen.append(c)
        if len(chosen)>=12:break
    return {'version':1,'source_frames':len(frames),'fps':fps,'review_required':True,'candidates':chosen,'limitations':'Advisory pose/velocity/duration ranking only; does not classify whole action, contact, prop integrity, unrequested effects or alpha/color quality. Inspect ALL source phases and complete candidates on dark/light.'}

def bridge_preview(output,frames,canvas,anchor):
    if not 2<=len(frames)<=1800:raise ValueError('Invalid bridge preview frame count')
    out=Path(output);out.mkdir(parents=True,exist_ok=False,mode=0o700);images=[];times=[];elapsed=0
    for f in frames:
        im=bridge_image(f)
        if im.size!=(canvas['width'],canvas['height']):raise ValueError('Bridge preview canvas differs; no silent resize or recenter')
        if not isinstance(f['duration_ms'],(int,float)) or not math.isfinite(f['duration_ms']) or f['duration_ms']<=0:raise ValueError('Invalid bridge preview duration')
        images.append(im);before=round(elapsed);elapsed+=f['duration_ms'];times.append(round(elapsed)-before)
    if elapsed>120000 or min(times)<1:raise ValueError('Bridge preview timing exceeds bounds')
    images[0].save(out/'sequence.apng',format='PNG',save_all=True,append_images=images[1:],duration=times,loop=1,disposal=0,blend=0)
    count=min(18,len(images));cols=min(6,count);sheet=Image.new('RGB',(cols*canvas['width'],math.ceil(count/cols)*(canvas['height']+20)*2));draw=ImageDraw.Draw(sheet)
    for i,index in enumerate(np.linspace(0,len(images)-1,count,dtype=int)):
        for side,bg in enumerate(('#141922','#f5f2e9')):
            image=Image.new('RGBA',images[index].size,bg);image.alpha_composite(images[index]);x=i%cols*canvas['width'];y=(i//cols+side*math.ceil(count/cols))*(canvas['height']+20);sheet.paste(image.convert('RGB'),(x,y+20));draw.text((x+2,y+2),frames[index].get('label',str(index)),fill='white')
    sheet.save(out/'contact-sheet.png');(out/'sequence.json').write_text(json.dumps({'scope':'Diagnostic full parent/phase-aware A -> ALL selected poses -> B. No new inference. Replay is not a gameplay seam.','canvas':canvas,'anchor':anchor,'duration_ms':elapsed,'frames':frames,'game_ready':False},indent=2)+'\n')
    return {'output':str(out),'preview':str(out/'sequence.apng'),'contact':str(out/'contact-sheet.png'),'frames':len(frames),'duration_ms':elapsed}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['check','plan','prepare','endpoint','reference_preview','validate','process','reprocess','bridge_analyze','bridge_preview']);args=parser.parse_args();data=json.load(sys.stdin)
    if args.action=='check':
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise ValueError('Install FFmpeg/ffprobe before starting; no generation attempted')
        result={'ready':True,'pillow':Image.__version__,'numpy':np.__version__}
    elif args.action=='plan':
        palette=data.get('palette',[])
        if data.get('reference'):palette+=image_palette(data['reference'])
        for reference in data.get('references',[]):palette+=image_palette(reference)
        result={'key':choose_key(palette,native=data.get('native',False)),'palette':palette,'guaranteed_absent':False}
    elif args.action=='prepare':result=prepare_source(**data)
    elif args.action=='reference_preview':result=reference_preview(**data)
    elif args.action=='bridge_analyze':result=bridge_analyze(**data)
    elif args.action=='bridge_preview':result=bridge_preview(**data)
    elif args.action=='endpoint':result=prepare_endpoint(**data)
    elif args.action=='validate':result=validate_source(**data)
    elif args.action=='reprocess':result=reprocess_export(**data)
    else:result=process_video(**data)
    print(json.dumps(result))

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);sys.exit(1)
