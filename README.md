<div align="center">
  <img src="images/logo.png" alt="AutoMorph Logo" width="340">
  <p><br>A self-hosted tool for automatically patching and distributing Android packages, built on Morphe Desktop.</p>
</div>

## Key Features

- **Automated repo tracking:** Monitors patch repositories for updates.
- **Automatic APK downloads:** Downloads base APKs automatically, prioritising Google Play and falling back to APKCombo, Uptodown, and APKPure.
- **Email notifications and web portal:** Get email alerts with direct download links whenever a patch succeeds, or download builds directly from the web portal. *A dedicated Android app for notifications and downloads is planned.*
- **Scheduled or manual builds:** Run automatically once a day at a time of your choice, or start a build manually through the web interface.
- **Free hosting:** Designed to run within the free tier of Oracle Cloud Infrastructure.
  - Works with free **Pangolin tunnels** or **Tailscale Funnel** for a URL to access the web portal and downloads.
  - Supports **Cloudflare Tunnels** if you use a custom domain.

## Screenshots

### Control Center & Dashboard
<!-- Screenshot placeholder: Save a screenshot of the main dashboard to images/dashboard.png -->
![AutoMorph Dashboard](images/dashboard.png)

### Add Package Wizard
![Add Package Wizard](images/add_package.png)

### Settings & Tunnels
![Settings & Tunnels](images/settings.png)

## Documentation

For full step-by-step guides, visit our **[Documentation Hub](docs/README.md)**:

* ☁️ **[Oracle Free Tier Setup Guide (Optional)](docs/oracle-setup.md)**: Optional guide to launch a free VPS on Oracle Cloud (skip if you already have your own server!).
* 🚇 **[Remote Access & Tunnels Guide](docs/tunnels.md)**: Expose your portal securely with free HTTPS via Pangolin, Tailscale Funnel, or Cloudflare Tunnels.
* 💻 **[Native Install (.deb / .rpm / .AppImage)](docs/native-install.md)**: Install without Docker via systemd packages or the 1-liner.
* 📖 **[User Guide & Web Interface](docs/user-guide.md)**: Managing packages, customizing patch options, daily schedules, and email notifications.

## Prerequisites

- Docker & Docker Compose **or** a native install (Debian/Ubuntu, Fedora/RHEL, Arch, or AppImage — no Docker needed)

## Install

### Option A — 1-liner (native .deb / .rpm / .AppImage)

```bash
curl -fsSL https://raw.githubusercontent.com/9M2PJU/AutoMorph/main/packaging/install.sh | sudo bash
```

Installs the matching `.deb`/`.rpm` (or `.AppImage` fallback), enables the
`automorph` systemd service, and serves the portal on `http://localhost:8080`.
See [docs/native-install.md](docs/native-install.md) for channels (`--channel deb|rpm|appimage|docker`),
version pins (`--version v1.2.3`), and manual `dpkg`/`dnf`/AppImage steps.

### Option B — Arch Linux (AUR)

```bash
yay -S automorph-git
# or: paru -S automorph-git
```

Then configure and start:

```bash
sudo nano /etc/automorph/.env
sudo systemctl enable --now automorph
```

PKGBUILD is maintained in [`packaging/aur/automorph-git`](packaging/aur/automorph-git); details in [docs/native-install.md](docs/native-install.md#arch-linux-aur).

### Option C — Docker Compose

1. Set up your preferred [remote tunnel](docs/tunnels.md) (Pangolin, Tailscale Funnel, or Cloudflare) to obtain your URL and tokens.
2. Download `compose.yaml` (from [`default_compose.yaml`](default_compose.yaml)) and [`example.env`](example.env).
3. Fill in your details and tunnel credentials in `example.env`, then rename it to `.env`.
4. Run:

   ```bash
   docker compose pull
   docker compose up -d
   ```

4. Open your web portal via your configured tunnel or local URL (`http://localhost:8080`). For detailed configuration, see the [User Guide](docs/user-guide.md).

## A Note on AI

AutoMorph started as a personal project to practise and improve my Python skills. I wrote the vast majority of the backend and core logic myself. The web interface was mostly written with AI assistance, with me guiding the development.

## Acknowledgements

- **Morphe Desktop**
- **Obtainium** - Heavily inspired the package scraping and discovery methods.
- **goopdl** - For downloads from Google Play: https://github.com/Villoh/goopdl
