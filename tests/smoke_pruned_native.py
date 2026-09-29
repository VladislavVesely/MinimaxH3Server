"""Opt-in CPU native import + synthetic AV codec smoke (NOT H3 inference).

Run in a separately provisioned sandbox with pinned ComfyUI/ComfyUI-GGUF folders:
PYTHONPATH=src <sandbox>/venv/bin/python tests/smoke_pruned_native.py <sandbox>
No weights, CUDA/MPS work, sockets or server startup are permitted.
"""
import os
from pathlib import Path
import socket
import sys

sandbox = Path(sys.argv[1]).resolve()
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                  CUDA_VISIBLE_DEVICES='-1', HF_HOME=str(sandbox/'cache/hf'),
                  TORCH_HOME=str(sandbox/'cache/torch'), TMPDIR=str(sandbox/'tmp'))
sys.dont_write_bytecode = True

def no_network(*args, **kwargs):
    raise AssertionError('network forbidden during native smoke')
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.socket.bind = no_network
socket.create_connection = no_network

from h3server.backends.comfy_runtime import verify_sources
from h3server.backends.catalog import get_artifact_set
from h3server.backends.pruned import load_runtime
from h3server.paths import AppPaths

paths = AppPaths(sandbox/'app')
for d in paths.directories(): d.mkdir(parents=True, exist_ok=True)
selected = get_artifact_set('h3-gguf-q4-k-m')
verify_sources(sandbox, selected.profile_name)
rt = load_runtime(sandbox, paths, paths.models/'unused', selected, cpu_smoke=True)
assert rt.mm.get_torch_device().type == 'cpu'
assert rt.nodes.CLIPLoader and rt.gguf.UnetLoaderGGUF
from comfy.quant_ops import QUANT_ALGOS
assert {'convrot_w4a4','int8_tensorwise','nvfp4'} <= QUANT_ALGOS.keys()
# Metadata conversion only (no weights/matmul): encoder old metadata must survive
# native CLIP's conversion seam, including the mixed format's linear_dtype.
import json
layers = {'encoder.layer': {'format':'convrot_w4a4','linear_dtype':'int8','group_size':256}}
converted, _ = rt.utils.convert_old_quants({}, metadata={'_quantization_metadata':json.dumps({'layers':layers})})
assert json.loads(bytes(converted['encoder.layer.comfy_quant'].tolist())) == layers['encoder.layer']
assert 'res_multistep' in __import__('comfy.samplers', fromlist=['SAMPLER_NAMES']).SAMPLER_NAMES
assert 'comfy_api_nodes.nodes_minimax' not in sys.modules
if 'server' in sys.modules:
    assert not hasattr(sys.modules['server'].PromptServer, 'instance')
import av
av.CodecContext.create('libx264','w'); av.CodecContext.create('aac','w')
with rt.torch.inference_mode():
    images = rt.torch.zeros((124,32,32,3), device='cpu')
    audio = {'waveform':rt.torch.zeros((1,2,round(124/24*32000)),device='cpu'), 'sample_rate':32000}
    video = rt.create_video.execute(images=images,fps=24.0,audio=audio,bit_depth=8,color_space='sRGB',codec='none')[0]
    output = sandbox/'synthetic.mp4'
    video.save_to(str(output))
with av.open(str(output)) as container:
    assert len(container.streams.video) == len(container.streams.audio) == 1
    assert container.streams.video[0].codec_context.name == 'h264'
    assert container.streams.audio[0].codec_context.name == 'aac'
    assert container.streams.audio[0].rate == 32000
    frames = list(container.decode(video=0))
    assert len(frames) == 124 and frames[0].width == frames[0].height == 32
import h3server.remote.app  # Application dependency constraints coexist with native runtime.
from h3server.backends.h3 import validate_media
from h3server.domain import GenerationSettings
validate_media(output, GenerationSettings(width=32,height=32,profile=selected.profile_name))
verify_sources(sandbox, selected.profile_name)
print('PASS: pinned native imports, CPU selection, sampler registry, GGUF/native loaders, V3 CreateVideo and synthetic H264/AAC mux/decode. No model or GPU inference.')
