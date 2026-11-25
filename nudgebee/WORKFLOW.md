# NudgeBee: "Sangeet Rush" self-healing workflow

Milan runs in the `milan` namespace on Kubernetes. When the toast starts and hundreds of
phones join at once, Sarvam calls queue up (`milan_sarvam_inflight`) and caption latency
(`milan_utterance_seconds`) climbs. NudgeBee watches the workload, explains *why*, and,
after a human approves, fixes it with two actions: **scale pods** and **switch Milan to rush
mode in Flagsmith** (captions only, so no guest misses a word).

## 1. Connect the cluster

1. Create the kind cluster and deploy Milan (`make kind-up deploy`, see README).
2. NudgeBee → **Add cluster / Install agent** → choose *local (kind/minikube)* and run the
   Helm command it shows (docs: Installation → Agent → Local setup).
3. Check that the `milan` namespace and the `milan` deployment show up in NudgeBee.

## 2. Build the workflow (Workflow Builder → New workflow → "Sangeet Rush")

| # | Task (NudgeBee name) | Configuration |
|---|----------------------|---------------|
| 0 | **Trigger** | Manual for the live demo, or an alert/event trigger on namespace `milan` (high latency, CPU or restarts) |
| 1 | **AI investigation / RCA** (optional but great on stage) | Target: deployment `milan`, namespace `milan`. NudgeBee explains the spike |
| 2 | **Core → Approval** | Type `in_app`. Message: `Guest rush on Milan. Scale milan to 3 pods and enable Flagsmith rush_mode (captions only)?` Branch: continue only on *approve* |
| 3 | **k8s.horizontal_rightsize** | Namespace `milan`, workload `milan` (Deployment), `scaling_mode: change_to`, value `3` |
| 4 | **integrations.http** | See below: turns on `rush_mode` in Flagsmith |
| 5 | **Discord notification** | "Rush mode ON, Milan scaled to 3 pods" (Integrations → Notifications → Discord) |

### Task 4: HTTP request to Flagsmith

First get the feature-state URL once:

```bash
python ops/flagsmith_toggle.py rush_mode --show-id
# PATCH https://api.flagsmith.com/api/v1/environments/<ENV_KEY>/featurestates/<ID>/
```

| Field | Value |
|-------|-------|
| url | the URL printed above |
| method | `PATCH` |
| headers | `{"Content-Type": "application/json"}` |
| body | `{"enabled": true}` |
| auth_type | `api_key` |
| api_header_name | `Authorization` |
| api_key | `Api-Key <your Flagsmith Organisation API key>` |

Expected output: `status_code: 200`. Every Milan pod picks the change up within ~5 s (local
evaluation refresh) and every guest screen shows the **Rush mode** banner.

## 3. The "Rush over" workflow

Clone it: approval → `k8s.horizontal_rightsize` `change_to 1` → HTTP body `{"enabled": false}`.

## Why this design

* **Approval-gated:** a human (the event manager) stays in control of production changes.
* **Two levers, two speeds:** a flag flip helps within seconds; new pods take ~20 s. Together
  there's no gap.
* **No HPA on purpose:** NudgeBee owns scaling for this demo, so an HPA won't fight its
  decisions. In real production you'd let NudgeBee tune the HPA's min/max instead.
