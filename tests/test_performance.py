import pytest
from h3server.domain import GenerationSettings, JobRequest
from h3server.performance import PerformanceStore, configuration, Measurements


def test_hardware_cache_and_configuration_separate_averages(tmp_path):
    store=PerformanceStore(tmp_path/'performance.sqlite')
    request=JobRequest('NEVER EXPORT THIS', settings=GenerationSettings())
    config=configuration(request)
    assert 'NEVER EXPORT' not in str(config) and 'prompt' not in config
    hardware={'cpu_model':'fixture', 'runtime_revision':'a'}
    for i,total in enumerate((10,20)):
        store.record(str(i)*32,config,hardware,{'total_seconds':total,'stages':{'loading':2,'denoising':total-2},'sampling_steps':[1,2]},'warm',{'sparse':'off','sage':False})
    estimate=store.estimate(config,hardware,'warm')
    assert estimate['samples']==2 and estimate['mean_seconds']==15
    assert estimate['stages']['loading']['mean_seconds']==2
    assert estimate['sampling_steps'][0]['mean_seconds']==1
    assert store.estimate(config,hardware,'cold')['samples']==0
    assert store.estimate(config,{**hardware,'cpu_model':'other'},'warm')['samples']==0
    assert store.estimate({**config,'width':256},hardware,'warm')['samples']==0
    store.record('0'*32,config,hardware,{'total_seconds':100,'stages':{},'sampling_steps':[]},'warm',{'sparse':'off','sage':False})
    assert store.estimate(config,hardware,'warm')['samples']==2
    assert 'NEVER EXPORT' not in str(store.export())
    assert PerformanceStore(store.path).estimate(config,hardware,'warm')['samples']==2


def test_only_finite_complete_measurements_and_no_private_hardware(tmp_path):
    store=PerformanceStore(tmp_path/'performance.sqlite')
    config=configuration(JobRequest('test', settings=GenerationSettings()))
    with pytest.raises(ValueError):store.record('a'*32,config,{'hostname':'private'}, {'total_seconds':2,'stages':{},'sampling_steps':[]},'cold',{})
    with pytest.raises(ValueError):store.record('a'*32,config,{}, {'total_seconds':float('nan'),'stages':{},'sampling_steps':[]},'cold',{})


def test_prompt_only_change_reuses_nearest_measured_length_without_mixing_cache_or_settings(tmp_path):
    store = PerformanceStore(tmp_path / 'performance.sqlite')
    config = configuration(JobRequest('short prompt', settings=GenerationSettings()))
    metrics = {'total_seconds': 90, 'stages': {'denoising': 80}, 'sampling_steps': [4]}
    store.record('a'*32, config, {}, metrics, 'warm', {'sage':False,'sparse':'off'})
    changed = {**config, 'prompt_length_bucket': 1024, 'seed': 123}
    estimate = store.estimate(changed, {}, 'warm')
    assert estimate['samples'] == 1
    assert estimate['mean_seconds'] == 90
    assert estimate['match'] == 'nearby_prompt_length'
    assert estimate['reference_prompt_length_bucket'] == 256
    assert store.estimate(changed, {}, 'cold')['samples'] == 0
    assert store.estimate({**changed, 'steps':20}, {}, 'warm')['samples'] == 0
    assert store.estimate(changed, {'cpu_model':'different'}, 'warm')['samples'] == 0
    store.record('b'*32, changed, {}, {**metrics,'total_seconds':100}, 'warm', {'sage':False,'sparse':'off'})
    assert store.estimate(changed, {}, 'warm')['mean_seconds'] == 100
    store.delete_job('b'*32)
    assert store.estimate(changed, {}, 'warm')['mean_seconds'] == 90
    store.delete_job('a'*32)
    assert store.estimate(changed, {}, 'warm')['samples'] == 0


def test_measurements_count_actual_steps_without_log_reset():
    from h3server.domain import Event,StageStatus,Progress
    clock=iter([0,1,2,4,5,6,7])
    m=Measurements(clock=lambda:next(clock))
    m.observe(Event('denoising',StageStatus.RUNNING))
    m.observe(Event('denoising',StageStatus.RUNNING,'log'))
    m.observe(Event('denoising',StageStatus.RUNNING,progress=Progress(1,'steps',2)))
    m.observe(Event('denoising',StageStatus.RUNNING,progress=Progress(2,'steps',2)))
    m.observe(Event('denoising',StageStatus.COMPLETED))
    result=m.result()
    assert result['sampling_steps']==[3,1]
    assert result['stages']['denoising']==5
