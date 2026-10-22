# Builds aislados en Kubernetes (`MF_EXECUTION_MODE=k8s`)

Cada trabajo de la plataforma (análisis, migración, modernización…) corre en **su propio pod efímero**: el worker solo crea el
Job, espera y lo borra (`mf/worker/k8s_runner.py`). Nada sobrevive al trabajo: `/work`, `/tmp` y la caché de Maven son
`emptyDir` del pod.

| Control | Dónde |
|---|---|
| gVisor (kernel en espacio de usuario entre los plugins de Maven y el nodo) | `runtimeClassName: gvisor` (`10-runtimeclass-gvisor.yaml`; runsc instalado en los nodos) |
| Sin root, raíz de solo lectura, sin capacidades, seccomp RuntimeDefault, sin token de la API | manifiesto del Job (`job_manifest`) y namespace con Pod Security `restricted` |
| Red: solo DNS, PostgreSQL, Redis y el proxy de salida | `30-networkpolicy-runner.yaml` (requiere un CNI que aplique NetworkPolicy) |
| Salida solo a repositorios aprobados | `40-egress-proxy.yaml` (squid con lista permitida); Maven lo usa con `MF_MAVEN_PROXY`, git/osv con `HTTPS_PROXY` |
| El despachador solo puede crear/leer/borrar Jobs de su namespace | `20-dispatcher-rbac.yaml` |
| Secretos | Secret `mf-runner-env` (envFrom): `MF_DATABASE_URL`, `MF_REDIS_URL`, `MF_CRED_*`, `MF_S3_*`, `MF_MAVEN_PROXY`, `HTTPS_PROXY` |

Requisitos: artefactos en S3 (`MF_ARTIFACT_BACKEND=s3`; no hay volumen compartido entre pods) y la imagen del worker
publicada (`MF_K8S_IMAGE`, la que construye el workflow de despliegue).

```bash
kubectl apply -f deploy/k8s/
kubectl -n migration-factory create secret generic mf-runner-env --from-env-file=runner.env   # nunca en Git
# worker/despachador: misma imagen con MF_EXECUTION_MODE=k8s, MF_K8S_IMAGE=<imagen>, serviceAccountName: mf-dispatcher
```
