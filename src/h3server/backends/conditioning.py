"""Decode managed, prevalidated source assets only inside the inference worker."""
import math
from h3server.assets import AssetStore, sniff


def image(stream, torch):
    from PIL import Image, ImageOps
    import numpy as np
    Image.MAX_IMAGE_PIXELS=16_777_216
    with Image.open(stream) as im:
        im=ImageOps.exif_transpose(im).convert('RGB')
        return torch.from_numpy(np.array(im)).float().div_(255).unsqueeze(0)


def video(stream,torch,settings):
    import av
    import numpy as np
    _,_,format=sniff(stream)
    output=[];first=None;index=0;last=None
    with av.open(stream,format=format,options={'protocol_whitelist':'file,pipe','enable_drefs':'0'}) as container:
        source=container.streams.video[0]
        scale=min(1.,math.sqrt(min(settings.width*settings.height,1344*768)/(source.width*source.height)))
        w=max(32,round(source.width*scale/32)*32);h=max(32,round(source.height*scale/32)*32)
        for i,frame in enumerate(container.decode(source)):
            if i>=900:raise ValueError('Reference video decode bound')
            t=float(frame.time) if frame.time is not None else i/float(source.average_rate)
            if first is None:first=t
            t-=first
            if t>15.05:raise ValueError('Reference video duration bound')
            current=frame.reformat(width=w,height=h,format='rgb24').to_ndarray()
            while index/24<=t and index<settings.num_frames:
                output.append(current if last is None or index/24 >= t-1e-7 else last);index+=1
            last=current
            if index>=settings.num_frames:break
        end=min(15., t+1/float(source.average_rate)) if last is not None else 0
        while index/24<end and index<settings.num_frames:output.append(last);index+=1
    if len(output)<5:raise ValueError('Reference video has fewer than 5 usable 24fps frames')
    n=len(output)-(len(output)-5)%17
    return torch.from_numpy(np.stack(output[:n])).float().div_(255)


def audio(stream,torch):
    import av
    import numpy as np
    _,_,format=sniff(stream)
    arrays=[];samples=0
    resampler=av.AudioResampler(format='fltp',layout='stereo',rate=32000)
    with av.open(stream,format=format,options={'protocol_whitelist':'file,pipe'}) as container:
        for frame in container.decode(audio=0):
            for block in resampler.resample(frame):
                samples+=block.samples
                if samples>481600:raise ValueError('Reference audio duration bound')
                arrays.append(block.to_ndarray())
        for block in resampler.resample(None):arrays.append(block.to_ndarray())
    if not arrays:raise ValueError('Empty reference audio')
    return dict(waveform=torch.from_numpy(np.concatenate(arrays,axis=1)).unsqueeze(0),sample_rate=32000)


def load(rt,settings):
    store=AssetStore(rt.paths)
    store.conditioning(settings)
    if settings.input_mode=='frames':
        result={}
        for name in ('first_frame','last_frame'):
            id=getattr(settings,name);result[name]=None
            if id:
                with store.open(id) as source:result[name]=image(source,rt.torch)
        return result
    result={}
    for name,prefix,decoder in (('reference_images','ref_image',image),('reference_videos','ref_video',video),('reference_audios','ref_audio',audio)):
        values={}
        for i,id in enumerate(getattr(settings,name),1):
            with store.open(id) as source:values[f'{prefix}_{i}']=decoder(source,rt.torch,settings) if decoder is video else decoder(source,rt.torch)
        result[prefix+'s']=values
    return result
