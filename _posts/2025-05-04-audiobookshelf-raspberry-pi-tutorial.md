---
layout: post
title: creating a private audiobook server
date: 2025-05-04 17:00:00
description: A complete guide to building a private audiobook server with Raspberry Pi, Audiobookshelf, and Cloudflare Tunnel.
tags: hacking
categories: sample-posts
giscus_comments: true
---
The following tutorial is completely written by Claude after a sucessful session of vibe-coding to setup a private audiobook server for me and my friends, which I currently serve at [audiobooks.carlossanchezmunoz.com](https://audiobooks.carlossanchezmunoz.com). 
Not a very human text, but hey, this was useful to me, and we had to fix some initial mistakes by Claude along the way, so I guess the final recollection can be pretty useful, either for my future self or anyone interested.

# Self-hosted Audiobook Server with Raspberry Pi, Audiobookshelf & Cloudflare Tunnel

A complete guide to building a silent, always-on audiobook server you can stream from anywhere in the world — for you, your family, or anyone you share it with. No screen, no keyboard, no subscription fees.

---

## What you'll end up with

- A headless Raspberry Pi server running **Audiobookshelf**
- Accessible from **anywhere in the world** via a personal domain with HTTPS
- **Multiple users** with independent listening progress (you and your partner, for example)
- Fully controllable from your Mac via SSH — no monitor ever needed
- Streaming audio without downloading files to your phone

---

## What you need

- Raspberry Pi 4 or 5 (2GB RAM minimum recommended)
- MicroSD card (for OS) — 16GB or more
- External USB SSD or HDD for your audiobook library (optional for testing, recommended for real use)
- A Mac to set everything up from
- A domain managed on Cloudflare (free account is enough)
- A Cloudflare account

---

## Hardware notes

### Storage

For a permanent setup, avoid storing your audiobook library on the microSD card — they wear out under constant read/write. A USB SSD is the best option: silent, fast, and reliable. A 1TB USB SSD costs around 60-85€ and is more than enough for thousands of audiobooks (each one is typically 200–500MB).

If you have a **Raspberry Pi 5**, you can add an NVMe HAT (~20€) and a 1TB NVMe SSD (~45€) for the fastest possible setup, well within 100€.

### Cooling

For this use case — audio streaming and light home automation — the Pi runs at very low CPU. You don't need an active fan. A **passive heatsink case** (like the Flirc or Argon ONE) keeps temperatures around 50–55°C silently. Just heatsink stickers (~3€) are also enough for light workloads.

---

## Step 1 — Flash the OS on your Mac

Download and install **Raspberry Pi Imager** from [raspberrypi.com](https://www.raspberrypi.com/software/).

1. Open Imager → **Choose OS** → *Raspberry Pi OS Lite (64-bit)* — no desktop environment needed, saves resources
2. **Choose Storage** → select your SD card
3. Click the **gear icon ⚙️** before writing — this is critical for headless setup:
   - **Hostname**: `pi-home` (or whatever you prefer)
   - **Enable SSH** ✅
   - **Username**: `carlos` (use your own name)
   - **Password**: choose a strong one
   - **WiFi**: enter your network credentials — or skip if using ethernet (recommended)
   - **Locale / Timezone**: set to your region
4. Write the image

> **Tip:** Ethernet is better than WiFi for a permanent home server — more stable, faster file transfers, less CPU overhead, and one less thing that can break.

---

## Step 2 — First boot

- Insert the SD card into the Pi
- Connect ethernet cable if using wired connection
- Power on the Pi
- Wait 2 minutes for the first boot to complete

---

## Step 3 — Connect from your Mac via SSH

Open **Terminal** on your Mac:

```bash
ssh carlos@pi-home.local
```

If that doesn't resolve, find the Pi's IP from your router admin panel and use:

```bash
ssh carlos@192.168.1.XXX
```

You're now controlling the Pi from your Mac. You'll never need a screen or keyboard.

### Optional: passwordless SSH

Save yourself from typing the password every time:

```bash
ssh-copy-id carlos@pi-home.local
```

### Optional: keep SSH sessions alive

Add this to `~/.ssh/config` on your Mac:

```
Host pi-home.local
  ServerAliveInterval 60
```

---

## Step 4 — Update the system

```bash
sudo apt update && sudo apt upgrade -y
```

---

## Step 5 — Mount your external drive (skip for testing)

If you're just testing with the SD card, skip this step and come back to it when you're ready for a permanent setup.

```bash
# Find the drive name (usually /dev/sda1)
lsblk

# Create mount point
sudo mkdir /mnt/audiobooks

# Format if brand new
sudo mkfs.ext4 /dev/sda1

# Mount it
sudo mount /dev/sda1 /mnt/audiobooks

# Auto-mount on reboot
echo '/dev/sda1 /mnt/audiobooks ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
```

> If testing with SD card only, Audiobookshelf will store files at `/home/carlos/audiobooks` instead.

---

## Step 6 — Install Docker and run Audiobookshelf

Docker is the easiest way to run Audiobookshelf — no dependency management, easy updates, clean removal.

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker carlos
```

Log out and back in so the group change takes effect:

```bash
exit
# SSH back in, then:

docker run -d \
  --name audiobookshelf \
  --restart unless-stopped \
  -p 13378:80 \
  -v /home/carlos/audiobooks:/audiobooks \
  -v /home/carlos/abs/config:/config \
  -v /home/carlos/abs/metadata:/metadata \
  ghcr.io/advplyr/audiobookshelf
```

> If using external drive, replace `/home/carlos/audiobooks` with `/mnt/audiobooks`.

---

## Step 7 — Access Audiobookshelf on your local network

Open your browser on any device connected to your home WiFi:

```
http://pi-home.local:13378
```

Or using the IP directly:

```
http://192.168.1.XXX:13378
```

Find the IP anytime with:

```bash
hostname -I
```

Create your admin account on first access and set the library path to `/audiobooks`.

---

## Step 8 — Set up multiple users

Audiobookshelf has full multi-user support built in. Each user gets completely independent listening progress, bookmarks, history, and preferences — sharing the same library.

Go to: **Settings → Users → Create User**

Give each family member their own username and password. They log in on their phone with their own account and everything is tracked separately.

---

## Step 9 — Copy audiobooks to the Pi

The easiest way on Mac is **Cyberduck** — free, works like Finder, connects via SFTP.

1. Download [Cyberduck](https://cyberduck.io) (free)
2. New Connection → **SFTP**
3. Server: `pi-home.local`, Port: `22`, Username: `carlos`, Password: your Pi password
4. Drag and drop your audiobook files to `/home/carlos/audiobooks`

### Fix permissions if needed

If Cyberduck shows "permission denied":

```bash
sudo chown -R carlos:carlos /home/carlos/audiobooks
```

### Alternative: mount as Finder network drive

In Finder → **Go → Connect to Server** (⌘K) → enter `sftp://carlos@pi-home.local`

### After copying files

In Audiobookshelf → **Libraries** → click **Scan** to pick up new files.

---

## Step 10 — Expose to the internet with Cloudflare Tunnel

This lets you and anyone you share it with stream from anywhere in the world, with automatic HTTPS — no port forwarding, no VPN required.

**Prerequisites:** Your domain must be managed on Cloudflare (free account).

### Install cloudflared

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 -o cloudflared
sudo mv cloudflared /usr/local/bin/
sudo chmod +x /usr/local/bin/cloudflared
```

### Authenticate with Cloudflare

```bash
cloudflared tunnel login
```

It prints a URL — open it on your Mac, select your domain, and authorize it.

### Create the tunnel

```bash
cloudflared tunnel create audiobooks
```

Note the **tunnel ID** it returns (a UUID string).

### Create the config file

```bash
sudo mkdir -p /etc/cloudflared
sudo nano /etc/cloudflared/config.yml
```

Paste this (replace with your tunnel ID and domain):

```yaml
tunnel: YOUR-TUNNEL-ID
credentials-file: /etc/cloudflared/YOUR-TUNNEL-ID.json

ingress:
  - hostname: audiobooks.yourdomain.com
    service: http://localhost:13378
  - service: http_status:404
```

### Copy credentials to system path

```bash
sudo cp ~/.cloudflared/*.json /etc/cloudflared/
```

### Add DNS record

```bash
cloudflared tunnel route dns audiobooks audiobooks.yourdomain.com
```

This automatically creates the CNAME in Cloudflare — no dashboard needed.

### Test it

```bash
cloudflared tunnel run audiobooks
```

Open `https://audiobooks.yourdomain.com` — it should work with HTTPS automatically.

### Run on boot

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

Check it's running:

```bash
sudo systemctl status cloudflared
```

---

## Step 11 — Install the mobile app

Audiobookshelf streams audio directly — no need to download files to your phone.

**iOS options:**
- **Still** — clean, minimalist, native iOS, available on the App Store
- **SoundLeaf** — premium feel, CarPlay support, App Store
- **ShelfPlayer** — open source, feature-rich, App Store

**Android:**
- Official Audiobookshelf app (available on the Play Store / direct APK)

On first launch, enter your server URL:

```
https://audiobooks.yourdomain.com
```

Then log in with your Audiobookshelf username and password.

---

## Quick reference

| Task | Command / URL |
|---|---|
| SSH into Pi | `ssh carlos@pi-home.local` |
| Find Pi IP | `hostname -I` |
| Local access | `http://pi-home.local:13378` |
| Remote access | `https://audiobooks.yourdomain.com` |
| Check Docker containers | `docker ps` |
| Restart Audiobookshelf | `docker restart audiobookshelf` |
| Update Audiobookshelf | `docker pull ghcr.io/advplyr/audiobookshelf && docker restart audiobookshelf` |
| Check Cloudflare tunnel | `sudo systemctl status cloudflared` |
| Restart Cloudflare tunnel | `sudo systemctl restart cloudflared` |

---

## Architecture overview

```
Phone / any device
        │
        │ HTTPS
        ▼
Cloudflare Tunnel
        │
        │ HTTP (local)
        ▼
Raspberry Pi
  ├── Docker: Audiobookshelf (:13378)
  ├── Docker: Home Assistant (:8123)  [optional]
  └── Storage: USB SSD / SD card
```

---

*Built and documented from a real setup session. Total time from scratch to streaming: under an hour.*
