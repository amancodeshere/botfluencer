# GPU workstation

The team workstation is reached from the Mac with `ssh gaming-pc`, through
Tailscale and Windows port forwarding to Ubuntu 24.04 under WSL2. It has an RTX
3070 with 8 GiB VRAM. SSH configuration and private keys stay outside this repo.
The operating system, Tailscale device name, and SSH alias are separate names.

## Locations

- Windows checkout: `C:\Users\abhay\OneDrive\Desktop\botfluencer`.
- Same checkout in Ubuntu: `/mnt/c/Users/abhay/OneDrive/Desktop/botfluencer`.
- Miniforge: `/mnt/d/botfluencer/miniforge3`.
- Named Mamba environment: `botfluencer` (Python 3.13).
- Model/package caches: `/mnt/d/botfluencer/cache`.
- Private datasets and checkpoints: `/mnt/d/botfluencer/data` and
  `/mnt/d/botfluencer/runs`.

The Desktop is OneDrive-backed. Keep credentials, notebooks with competition
outputs, datasets, and checkpoints outside that directory, even if Git ignores
them. Ubuntu's virtual disk is on C:, which had little free space at setup;
the large environment and training artifacts therefore live on D:. Linux packages
on a Windows-mounted drive can have slower startup and file I/O. A future WSL
disk relocation can improve this, but requires separate shutdown planning.

## Activate and run

In a new Ubuntu terminal:

```sh
mamba activate botfluencer
cd /mnt/c/Users/abhay/OneDrive/Desktop/botfluencer
uv run --locked --inexact --extra training \
  --env-file /home/aman/.config/botfluencer/runtime.env botfluencer doctor
uv run --locked --inexact --extra training python scripts/check_gpu.py \
  --output-root /mnt/d/botfluencer/runs
```

The environment's non-secret variables set `UV_PROJECT_ENVIRONMENT` to the Mamba
prefix and caches to D:. Use `--inexact` to preserve Conda-managed packages. Do
not run a bare `uv sync` that removes training dependencies or creates a second
environment on the Desktop. For noninteractive SSH/service commands, use the
installed `botfluencer-workstation` launcher, which activates the environment
and enters the checkout before running uv:

```sh
botfluencer-workstation --env-file /home/aman/.config/botfluencer/runtime.env \
  botfluencer doctor
botfluencer-workstation python scripts/check_gpu.py \
  --output-root /mnt/d/botfluencer/runs --pretrained
```

The pretrained diagnostic downloads Microsoft's DeBERTa-v3-small, takes three
CUDA optimizer steps on synthetic examples, and verifies checkpoint reload.
It does not train a useful detector or estimate competition performance. Default
mode uses a random tiny BERT and requires no model download. Both require CUDA
and fail rather than silently falling back to CPU.

## Reproduce

Install Miniforge from the official conda-forge release and verify its published
SHA-256. Create the environment with:

```sh
mamba env create -f environments/gaming-pc.yml
mamba activate botfluencer
export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
export UV_CACHE_DIR=/mnt/d/botfluencer/cache/uv
export HF_HOME=/mnt/d/botfluencer/cache/huggingface
export HF_DATASETS_CACHE=/mnt/d/botfluencer/cache/datasets
export TORCH_HOME=/mnt/d/botfluencer/cache/torch
uv sync --locked --inexact --extra training
python -m ipykernel install --user --name botfluencer \
  --display-name 'Python (botfluencer CUDA)'
```

Core and training dependencies share `uv.lock`. The optional `training` extra
selects PyTorch from an explicit official CUDA 13.0 index on Linux x86_64;
Railway's normal install does not include it. The Windows NVIDIA driver provides
WSL GPU access. Do not install a Linux NVIDIA kernel driver in Ubuntu or assume
the driver's displayed CUDA maximum is the installed PyTorch runtime version.

JupyterLab and the `botfluencer` kernel are available for local notebooks. Bind
Jupyter to localhost and reach it through SSH forwarding; do not expose a public
notebook server or disable its authentication. Start notebooks outside OneDrive.
For example, run `jupyter lab --ip=127.0.0.1 --no-browser` from
`/home/aman/botfluencer-notebooks`, and open a separate Mac terminal with
`ssh -L 8888:127.0.0.1:8888 gaming-pc`. Use Jupyter's authenticated localhost URL.

Use small batches first (for example 2-4, max length 128-256) and gradient
accumulation. The diagnostic loads FP32 model weights explicitly and defaults to
FP32 computation. Autocast does not convert already-loaded half-precision weights
into FP32 optimizer parameters; keep that distinction when adding training code.
Validate mixed precision with the exact model and stack. Benchmark one classifier
before scheduling multiple experiments. The GPU also serves Windows; available
VRAM is below its total.

## Private settings and operating limits

Private environment files belong under `/home/aman/.config/botfluencer` with
directory mode 0700 and file mode 0600. Use an explicit `--env-file` there; never
put application/test credentials in OneDrive. Test jobs receive only the
dedicated test database URL. No local Docker database is required.

No bot/reporting service is enabled by workstation installation. Competition
network access and publishing retain the repository's explicit gates. A deployed
model, a labelled training dataset, and a reporting strategy are separate work.
SSH reachability after reboot still requires Windows startup of WSL and refreshing
the forwarding target if Ubuntu's address changes; enabling a systemd unit alone
does not implement that Windows startup layer.

`scripts/windows/install-startup.ps1` prepares that layer. Run it once in
Administrator PowerShell as the Windows user who owns Ubuntu, with the PC and
allowed Mac's Tailscale IPv4 addresses supplied as `-ListenAddress` and
`-AllowedClient`. It copies a protected script into ProgramData, enables
unattended Tailscale, and registers an S4U task at startup/logon without storing
a password. The task keeps WSL running and refreshes forwarding when its address
changes; the firewall permits only the supplied Mac. It does not alter Windows
automatic sign-in or power settings. The SSH session is not Windows-elevated, so
this installer cannot be applied from the normal remote session. Syntax checks
do not prove reboot recovery: inspect the task and its startup log and perform a
planned reboot before depending on unattended access. Tailscale key expiry and
Windows sleep still affect availability.

Sources: [Mamba installation](https://mamba.readthedocs.io/en/latest/installation/mamba-installation.html),
[uv syncing](https://docs.astral.sh/uv/concepts/projects/sync/),
[CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/index.html).
