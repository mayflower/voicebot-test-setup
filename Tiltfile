allow_k8s_contexts('k3d-voicebot-test-setup')

IMAGE = 'ghcr.io/mayflower/voicebot-test-setup'

# chart/ is what ArgoCD deploys: values.yaml plus the SOPS-decrypted
# values.sops.yaml. Without a GPG key that decrypts it, put your own
# LiveKit credentials under `secrets:` in values.local.yaml instead.
values = ['chart/values.yaml']
watch_file('chart/values.sops.yaml')
if local('sops -d chart/values.sops.yaml > .values.decrypted.yaml 2>/dev/null && echo ok || rm -f .values.decrypted.yaml', quiet=True):
    values.append('.values.decrypted.yaml')
if os.path.exists('values.local.yaml'):
    values.append('values.local.yaml')

k8s_yaml(helm(
    'chart',
    name='voicebot-test-setup',
    values=values,
    set=['imagePullSecrets=null', 'image.pullPolicy=IfNotPresent'],
))
# No live update: the agents CLI no longer reloads in-process, so a synced file
# would sit unused. An edit rebuilds only the last image layer and rolls the pod.
docker_build(IMAGE, '.')
k8s_resource('voicebot-test-setup')
