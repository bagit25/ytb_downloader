# Tubeget

Small web app: paste a YouTube link, preview it, and get an **MP3**, an **MP4** (360p–1080p) or a **vertical Short** (9:16, first 60 s, 1080×1920).
Built with FastAPI + yt-dlp + ffmpeg, designed to run on an Oracle Cloud *Always Free* VM, deployed automatically from GitHub.

```
ytb_downloader/
├── app/
│   ├── main.py          # routes: page, /api/info, /api/jobs, /healthz
│   ├── downloader.py    # yt-dlp wrapper, job queue, Short conversion, cleanup
│   ├── config.py        # settings from environment variables
│   ├── templates/index.html
│   └── static/          # style.css, app.js, favicon.svg
├── deploy/
│   ├── setup.sh         # one-time VM bootstrap
│   ├── deploy.sh        # pull + restart (run by GitHub Actions)
│   ├── ytapp.service    # systemd unit
│   └── nginx.conf       # reverse proxy
├── .github/workflows/deploy.yml
├── requirements.txt
└── .env.example
```

## Run locally

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```
ffmpeg must be installed. Install [Deno](https://deno.land) too, because yt-dlp needs it to solve YouTube's JavaScript challenges.

---

## 1. Put the code on GitHub

1. On github.com, create a new repository named `ytb_downloader` and leave it empty (no README).
2. In this folder:
   ```bash
   git init -b main
   git add .
   git commit -m "Tubeget: FastAPI + yt-dlp, Oracle deploy"
   git remote add origin https://github.com/<USER>/ytb_downloader.git
   git push -u origin main
   ```

## 2. Prepare the Oracle VM

1. **Compute → Instances → Create instance**: Ubuntu 22.04/24.04, shape `VM.Standard.A1.Flex` (e.g. 1 OCPU / 6 GB). Download the SSH private key.
2. **Open ports in the console**: Networking → Virtual cloud networks → your VCN → Subnet → Default Security List → *Add Ingress Rules*: Source `0.0.0.0/0`, TCP, destination port `80,443`.
3. **Connect and bootstrap**:
   ```bash
   ssh -i oracle_key.key ubuntu@<PUBLIC_IP>
   curl -fsSLO https://raw.githubusercontent.com/<USER>/ytb_downloader/main/deploy/setup.sh
   bash setup.sh https://github.com/<USER>/ytb_downloader.git            # IP only
   # or: bash setup.sh https://github.com/<USER>/ytb_downloader.git tube.mydomain.com
   ```
   This step installs Python, Nginx, ffmpeg and Deno, opens the VM firewall, creates the service, and gets an HTTPS certificate when you give a domain (its DNS A record must already point to the VM). Then open `http://<PUBLIC_IP>/`.

> **Private repository?** On the VM, run `ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519`, add `~/.ssh/id_ed25519.pub` to GitHub (repo → Settings → Deploy keys, read-only), then use the `git@github.com:<USER>/ytb_downloader.git` URL with setup.sh.

## 3. Connect GitHub to Oracle (auto-deploy)

Every push to `main` then runs `deploy/deploy.sh` on the VM over SSH. The same workflow also runs every Monday to update yt-dlp.

1. On the VM, create a key just for GitHub Actions:
   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/gh_actions
   cat ~/.ssh/gh_actions.pub >> ~/.ssh/authorized_keys
   cat ~/.ssh/gh_actions          # copy the whole private key, BEGIN to END lines included
   ```
2. On GitHub, open repo → **Settings → Secrets and variables → Actions → New repository secret** and add:

   | Name | Value |
   |---|---|
   | `ORACLE_HOST` | the VM public IP |
   | `ORACLE_USER` | `ubuntu` |
   | `ORACLE_SSH_KEY` | the private key from step 1 |

3. Go to the **Actions** tab, open *Deploy to Oracle Cloud*, and click *Run workflow*. It should end green with `Deployed <commit>, yt-dlp <version>`.

From now on: edit the code, `git push`, and the site updates within about a minute.

---

## Settings

Edit `/home/ubuntu/ytb_downloader/.env` on the VM, then run `sudo systemctl restart ytapp`. See `.env.example` for all keys: duration limit, concurrent downloads, rate limit per IP, file lifetime and Short length.

## When YouTube blocks the server

Data-centre IPs (Oracle included) sometimes get *"Sign in to confirm you're not a bot"*. The app shows a clear message when this happens. To work around it:

1. Export cookies from a **secondary** YouTube account with a browser extension ("Get cookies.txt LOCALLY"), in Netscape format.
2. Copy them to the VM: `scp -i oracle_key.key cookies.txt ubuntu@<IP>:~/ytb_downloader/`
3. In `.env`, set `COOKIES_FILE=/home/ubuntu/ytb_downloader/cookies.txt` and restart.

A residential proxy (`PROXY=` in `.env`) is the other option. Cookies expire, so repeat when the error returns.

## Useful commands on the VM

```bash
sudo systemctl status ytapp          # is it running?
sudo journalctl -u ytapp -f          # live logs
bash ~/ytb_downloader/deploy/deploy.sh   # manual redeploy
df -h /tmp                           # disk used by temporary downloads
```

## Note

Downloading from YouTube is against its Terms of Service unless you own the content or have permission. Keep the instance for personal use, or limit it to content you have the rights to.
