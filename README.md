# voicebot-test-setup

A minimal SIP test bot for checking a phone setup before the real voicebot is deployed. It proves that calls reach LiveKit, that audio flows both ways, that key presses (DTMF) arrive, and that calls can be forwarded out again. There is no AI, database or web frontend.

## What a test call looks like

1. Call `SIP_NUMBER`. The bot answers silently and **echoes your voice back**: hearing yourself proves audio in both directions.
2. Press **any key**. The bot logs the digit and **forwards the call** to `TRANSFER_TO`.
3. When either side hangs up, the call ends for both.

There are two ways to forward, set by `TRANSFER_MODE`:

- **`bridge`** (default): LiveKit dials `TRANSFER_TO` through the bot's outbound trunk and puts the target into the same room as the caller. LiveKit stays in the call and mixes the audio. You hear a dial tone while it rings.
- **`refer`**: a cold transfer. LiveKit sends a SIP REFER on the caller's leg and the caller's SIP provider places the new call. LiveKit drops out of the call. The provider must accept REFER; Twilio, for example, only does once call transfer is enabled on the trunk.

If forwarding fails (no answer, REFER rejected), the bot logs why and goes back to echoing.

## What it creates

At startup the bot creates three objects on the LiveKit server, all named after `AGENT_NAME`:

| Object | Name | Purpose |
|---|---|---|
| Inbound trunk | `<AGENT_NAME>-inbound` | Accepts calls to `SIP_NUMBER` |
| Outbound trunk | `<AGENT_NAME>-outbound` | Places forwarded calls (`bridge` mode) through your SIP provider, with `SIP_NUMBER` as caller ID |
| Dispatch rule | `<AGENT_NAME>-dispatch` | Puts each call into its own room and sends this bot there |

Every start deletes the objects the previous start created and creates fresh ones. It never touches objects with other names. Two consequences:

- Two instances with the same `AGENT_NAME` fight over the same objects. Give every instance, including a local run, its own `AGENT_NAME` and `SIP_NUMBER`.
- No other inbound trunk on the LiveKit server may claim `SIP_NUMBER`.

The objects stay on the server after the bot stops. Calls to the number then find no agent.

## Prerequisites

- A LiveKit server with SIP enabled, and an API key and secret for it.
- A phone number whose calls your SIP provider sends to the LiveKit SIP endpoint.
- SIP credentials (address, username, password) that let LiveKit place outbound calls through your provider. These are only used in `bridge` mode.

## Values to set

`chart/values.yaml` contains placeholders only. Set real values for each deployment. When running locally, put them in `values.local.yaml`.

| Key | Example | Meaning |
|---|---|---|
| `env.LIVEKIT_URL` | `wss://livekit.example.com` | LiveKit server |
| `env.AGENT_NAME` | `voicebot-test-setup` | Agent name, and prefix of the objects it creates |
| `env.SIP_NUMBER` | `+4989123456` | Number calls arrive on, and caller ID of forwarded calls (E.164) |
| `env.SIP_OUTBOUND_ADDRESS` | `example.pstn.twilio.com` | SIP provider host forwarded calls leave through |
| `env.SIP_OUTBOUND_USERNAME` | `username` | Username for that SIP provider |
| `env.TRANSFER_MODE` | `bridge` | `bridge` or `refer`; the bot refuses to start on anything else |
| `env.TRANSFER_TO` | `+4917612345678` | Number a key press forwards to (E.164) |
| `secrets.LIVEKIT_API_KEY` | | LiveKit API key |
| `secrets.LIVEKIT_API_SECRET` | | LiveKit API secret |
| `secrets.SIP_OUTBOUND_PASSWORD` | | Password for the SIP provider |

The secrets live in `chart/values.sops.yaml`, which is SOPS-encrypted and empty in the repo. Fill it with `sops chart/values.sops.yaml`. Only the GPG keys listed in `.sops.yaml` can decrypt it. Without one of those keys, put the secrets under `secrets:` in `values.local.yaml` instead.

## Run locally

Needs [mise](https://mise.jdx.dev) and Docker.

```bash
cp values.local.yaml.example values.local.yaml   # fill in the values above
mise start                                       # k3d cluster + Tilt, UI on http://localhost:10350
mise stop                                        # deletes the cluster
```

Tilt renders the chart with `chart/values.yaml`, the decrypted `chart/values.sops.yaml` (if your key can decrypt it) and `values.local.yaml`, in that order. An edit to `agent.py` rebuilds the image and restarts the pod. The pod log shows the created trunk and rule IDs, and each call, key press and forward.

## Deploy with ArgoCD

`chart/` is a self-contained Helm chart. Point an ArgoCD Application at it with the value files `values.yaml` and `values.sops.yaml`, and override the placeholders. ArgoCD can decrypt `values.sops.yaml` only once its key is a recipient: add the key to `.sops.yaml` and run `sops updatekeys chart/values.sops.yaml`.

Every push to `main` builds `ghcr.io/mayflower/voicebot-test-setup:latest` and `:<sha>`. The package is private, so the namespace needs the `github-registry` pull secret (`imagePullSecrets` in `values.yaml`). Keep it at one replica: the chart replaces the pod rather than rolling it, so two pods never recreate the trunks at once.
