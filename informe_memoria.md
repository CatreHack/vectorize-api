# Diagnóstico de memoria — Render plan free

## Evidencia del propio servidor (`GET /api/mem`)

```json
{
    "meminfo_MemTotal_MB": 31386.7,     <- el HOST: 31 GB
    "meminfo_MemAvailable_MB": 13308.6, <- 13 GB "disponibles"
    "cgroup_v2_max": "536870912",
    "cgroup_v2_max_MB": 512.0,          <- MI LÍMITE REAL: 512 MB
    "cgroup_v2_current": "111599616",
    "rss_MB": 91.5,
    "meminfo_es_del_host": true         <- CONFIRMADO
}
```

## Conclusión

Mi guarda de memoria leía `/proc/meminfo`, que reporta la RAM del **host físico**
(31 GB), NO la del contenedor. Por eso veía "13 GB disponibles" cuando en realidad
solo tenía **512 MB**. La guarda nunca se activó y el proceso moría por OOM.

La única cifra válida es el **cgroup**: 512 MB.

## Solución aplicada

Decidir por umbral fijo (no por "memoria disponible"), porque en el contenedor esa
métrica es inútil.

## Límites reales del plan free de Render

| Recurso | Valor |
|---|---|
| RAM | **512 MB** (no negociable) |
| CPU | 0.1 vCPU compartida |
| Disco | efímero |
| Suspensión | a los 15 min sin tráfico |
| Build | puede morir por memoria en deps pesadas |

## Opciones con más memoria

### 1. AWS EC2 t3.small — **lo que el usuario ya conoce**
- 2 vCPU, **2 GB RAM** (t3.micro free tier = 1 GB; t3.small = 2 GB)
- Capa gratuita 12 meses solo en t2.micro/t3.micro (1 GB)
- Para 8 GB: t3.large (~$60/mes) o similar

### 2. Render pagado
- Starter $7/mes: 512 MB -> **2 GB RAM**, 0.5 CPU
- Standard $25/mes: 2 GB -> **4 GB RAM**, 1 CPU

### 3. Alternativas con capa gratuita generosa
| Plataforma | RAM gratis | Nota |
|---|---|---|
| Fly.io | 256 MB x3 VMs | poco |
| Railway | $5 crédito | 8 GB por servicio es posible |
| Koyeb | 512 MB | similar a Render |
| Hugging Face Spaces | **16 GB** gratis (CPU) | ideal demo, Docker |
| Google Cloud Run | 512 MB gratis, hasta 32 GB pago | escala a cero |
| Oracle Cloud Free | **24 GB RAM** gratis siempre | ARM Ampere, muy generoso |
