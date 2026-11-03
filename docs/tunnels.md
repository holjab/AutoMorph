[🏠 Documentation Hub](README.md) › **Remote Access & Tunnels Guide**

---

# Remote Access & Tunnels Guide

AutoMorph runs on port `8080` inside Docker. When hosting on a cloud VPS (such as [Oracle Free Tier](oracle-setup.md)) or on a home server behind NAT/CGNAT, exposing your instance securely without opening firewall ports or managing SSL certificates is best accomplished using a **Tunnel**.

AutoMorph comes with built-in, first-class support for three tunnel providers. You can choose any one (or even combine multiple):

| Feature | 🔴 Pangolin | 🔵 Tailscale Funnel | 🟠 Cloudflare Tunnel |
| :--- | :--- | :--- | :--- |
| **Cost** | 100% Free | 100% Free (Tailscale Free tier + zero-cost Funnel) | 100% Free (requires your own domain) |
| **Custom Domain Required?** | ❌ Optional (Free URL provided, or use custom domain) | ❌ No (`*.ts.net` provided) | ✅ Yes |
| **Access Type** | Public URL | Public (Funnel) or Private Mesh | Public URL |
| **SSL / HTTPS** | Automatic | Automatic | Automatic |
| **Requires Host Agent?** | ❌ In Docker Compose | ✅ Host CLI / Tailscale daemon | ❌ In Docker Compose |
| **Best For** | Quickest setup, free URL or custom domain | Tailscale network users, zero cost | Users with their own domain |

---

## Table of Contents

- [Option 1: Pangolin Setup (Easiest / Free URL)](#option-1-pangolin-setup-easiest--free-url)
  - [1.1 Create Account & Target](#11-create-account--target)
  - [1.2 Configure `.env`](#12-configure-env)
- [Option 2: Tailscale Funnel Setup](#option-2-tailscale-funnel-setup)
  - [2.1 Install Tailscale on the Host](#21-install-tailscale-on-the-host)
  - [2.2 Enable Tailscale Funnel](#22-enable-tailscale-funnel)
  - [2.3 Configure `.env`](#23-configure-env-1)
- [Option 3: Cloudflare Tunnel Setup](#option-3-cloudflare-tunnel-setup)
  - [3.1 Create a Cloudflare Tunnel](#31-create-a-cloudflare-tunnel)
  - [3.2 Route Traffic to AutoMorph](#32-route-traffic-to-automorph)
  - [3.3 Configure `.env`](#33-configure-env-2)
- [Starting Services & Viewing Logs](#starting-services--viewing-logs)
- [Tunnel Authentication & Mobile Apps (Cloudflare Access / Pangolin SSO)](#tunnel-authentication--mobile-apps-cloudflare-access--pangolin-sso)
  - [1. Path Bypass Rules (Recommended)](#1-path-bypass-rules-recommended)
  - [2. Extra Protection via Cloudflare WAF](#2-extra-protection-via-cloudflare-waf)
  - [3. Private Tailscale Network (Zero Public Access)](#3-private-tailscale-network-zero-public-access)
- [Repository Base URL & Ingress Selection (Optional)](#repository-base-url--ingress-selection-optional)
- [Troubleshooting](#troubleshooting)

---

## Option 1: Pangolin Setup (Easiest / Free URL)

Pangolin provides quick reverse-proxy tunnels using Newt clients. You don't need to purchase a domain or open firewall ports, though custom domains are fully supported if you have one.

### 1.1 Create Account & Target
1. Register for an account at [Pangolin](https://pangolin.net/) (or your self-hosted Pangolin instance).
2. Create a new Site/Target in your Pangolin dashboard:
   * **Target Host**: `automorph`
   * **Target Port**: `8080`
   * **Protocol**: `HTTP`
3. Generate or view your **Newt Client credentials**:
   * Note your **Endpoint**, **Newt ID**, and **Newt Secret**.
   * Note the allocated public domain/URL (e.g. `https://my-patcher.example-pangolin.com`, or your configured custom domain).

> [!TIP]
> Pangolin provides a free sub-domain out of the box, but you can also configure your own custom domain directly within your Pangolin dashboard if you prefer branded URLs.

### 1.2 Configure `.env`
In your `automorph` folder, open `.env`:
```bash
nano .env
```
Fill in the Pangolin variables:
```dotenv
# Enable the Pangolin compose profile
COMPOSE_PROFILES=pangolin

# Pangolin Credentials
PANGOLIN_ENDPOINT=app.pangolin.net:443
PANGOLIN_ID=your_newt_id_here
PANGOLIN_SECRET=your_newt_secret_here
PANGOLIN_URL=https://my-patcher.example-pangolin.com
```

Save with `Ctrl + O`, `Enter`, and exit with `Ctrl + X`.

---

## Option 2: Tailscale Funnel Setup

Tailscale allows you to access AutoMorph over your private tailnet or expose it publicly using [Tailscale Funnel](https://tailscale.com/kb/1223/funnel).

### 2.1 Install Tailscale on the Host
If you are on an Ubuntu VPS (such as Oracle):
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
Follow the authentication link printed in the terminal to sign into your Tailscale account.

### 2.2 Enable Tailscale Funnel
To expose port 8080 as a public HTTPS URL:
```bash
sudo tailscale funnel 8080
```
Tailscale will output your public address (e.g., `https://your-node.tailnet-xyz.ts.net`).

> [!NOTE]
> If you only want to access AutoMorph securely within your private Tailscale network (not publicly over the open internet), run `sudo tailscale serve 8080` instead of `funnel`.

### 2.3 Configure `.env`
Open your `.env` file:
```dotenv
TAILSCALE_FUNNEL_URL=https://your-node.tailnet-xyz.ts.net
```

> [!IMPORTANT]
> **Does Tailscale need `COMPOSE_PROFILES`?**
> **No.** Unlike Pangolin or Cloudflare (which run as separate sidecar containers in `compose.yaml`), Tailscale runs directly on your host operating system. You do **not** add `tailscale` to `COMPOSE_PROFILES`.
>
> **How does the email builder and web portal use Tailscale?**
> AutoMorph's dispatcher inspects your `.env` for `TAILSCALE_FUNNEL_URL`. As long as this URL is populated, AutoMorph automatically creates a branded **Tailscale Funnel** download button and badge in your email build notifications, and displays the direct download link on the web portal.

---

## Option 3: Cloudflare Tunnel Setup

If you manage your own domain through Cloudflare, Cloudflare Tunnels (formerly Argo Tunnel) provide robust DDoS protection and free SSL.

### 3.1 Create a Cloudflare Tunnel
1. Log into your [Cloudflare Dashboard](https://dash.cloudflare.com/) and navigate to **Zero Trust** > **Networks** > **Tunnels**.
2. Click **Create a Tunnel** and select **Cloudflared**.
3. Name your tunnel (e.g., `automorph-tunnel`) and click **Save Tunnel**.
4. In the installation command section, choose **Docker**. You will see a command containing a long token string:
   ```text
   docker run cloudflare/cloudflared:latest tunnel --no-autoupdate run --token eyJh...
   ```
   Copy only the token string (`eyJh...`).

### 3.2 Route Traffic to AutoMorph
Under the **Public Hostname** tab:
* **Subdomain**: e.g., `patcher` (or leave blank for root domain)
* **Domain**: select your registered domain (e.g., `yourdomain.com`)
* **Type**: `HTTP`
* **URL**: `automorph:8080` *(uses the Docker container service name)*

Click **Save hostname**.

### 3.3 Configure `.env`
Open your `.env` file:
```dotenv
# Enable the Cloudflare profile
COMPOSE_PROFILES=cloudflare

# Cloudflare Token & Public URL
CLOUDFLARE_TUNNEL_TOKEN=eyJh...your_token_here...
CLOUDFLARE_TUNNEL_URL=https://patcher.yourdomain.com
```

---

## Starting Services & Viewing Logs

After configuring your chosen tunnel in `.env`:

1. Start or reload Docker Compose:
   ```bash
   docker compose up -d
   ```
2. Verify that the tunnel container is running:
   ```bash
   docker compose ps
   ```
3. Check live tunnel logs to ensure connections are established:
   ```bash
   # For Pangolin
   docker compose logs -f pangolin

   # For Cloudflare
   docker compose logs -f cloudflared
   ```

---

## Tunnel Authentication & Mobile Apps (Cloudflare Access / Pangolin SSO)

If you place your AutoMorph domain behind an identity provider or login screen (such as **Cloudflare Zero Trust Access** or **Pangolin SSO / Authelia**), your web browser logs in via email PIN or Google/GitHub authentication.

However, mobile background updaters like **Obtainium** and **Neo Store** are automated tools, not interactive web browsers. When they query your repository or attempt to download an APK, the tunnel intercepts their request with an HTML login page, causing downloads to fail.

> [!NOTE]
> Because AutoMorph feeds integrate with Obtainium as an **F-Droid repository**, Obtainium does not offer a custom request headers option for this source (in Obtainium, custom headers are only supported on HTML and direct link sources). Therefore, mobile updaters cannot pass login cookies or service tokens.

To allow mobile app updates without compromising your server's security, use one of the strategies below:

### 1. Path Bypass Rules (Recommended)

Add a **Bypass** policy in your tunnel provider for only the two paths needed by your phone:

* **Paths to Bypass**:
  * `/files/*` (the APK downloads)
  * `/index-v2.json` (the repository index that tells your phone when an update is available)
* **What stays protected**: Your root dashboard (`/`), admin APIs (`/api/*`), build triggers, settings, and live console remain 100% protected behind your SSO login screen.
* **Is `/files/*` open to directory browsing?**: **No.** AutoMorph does not expose directory listings under `/files/*` (visiting any folder directly redirects into the dashboard). An outsider cannot see or browse what files exist unless they already know the exact package name, version, and APK filename.

### 2. Extra Protection via Cloudflare WAF

If you use Cloudflare and want extra protection on the bypassed `/files/*` path without breaking mobile updates, you can use Cloudflare's **WAF (Web Application Firewall)**:

* In your Cloudflare Dashboard, go to **Security** › **WAF** › **Custom Rules**.
* Create a rule targeting the download path:
  * **Country Restriction**: Restrict downloads to your own country (e.g. `(http.request.uri.path contains "/files/" and ip.geoip.country ne "GB") -> Block`).
  * **Rate Limiting**: Add a rate-limiting rule to block any IP address making more than 15–20 requests per minute.
  * **Bot Fight Mode**: Enable Cloudflare's automated bot protection to block automated web crawlers.

### 3. Private Tailscale Network (Zero Public Access)

If you do not want any public internet exposure at all:
* Use **Tailscale Serve** (private mesh) instead of a public tunnel or Funnel.
* Install the Tailscale app on your phone and connect to your tailnet.
* Your phone talks directly to AutoMorph's private node address (e.g. `http://automorph.tailnet.ts.net:8080`) over an encrypted WireGuard tunnel without needing any public bypasses or login screens.

---

## Repository Base URL & Ingress Selection (Optional)

AutoMorph automatically detects active tunnels configured in your `.env` (Pangolin, Cloudflare, or Tailscale) and assigns an active address for your Obtainium and F-Droid feeds by default.

If you want to customize or switch which address is used, you can do so in the web portal under **Settings** › **Repository Ingress & Base URL**:

* **Multiple Tunnels**: If you run more than one tunnel simultaneously (such as both Cloudflare and Pangolin), you can choose which tunnel address is embedded into your Obtainium export and F-Droid repository feeds for downloads.
* **Custom Outside URL**: If you host AutoMorph behind your own reverse proxy or port forward without an automated tunnel, select this option to enter your custom domain or LAN address.
* **Configured Tunnels Only**: The dropdown automatically filters the list to only display tunnels that have active credentials or profiles in `.env`, keeping unconfigured options hidden.
* **Local Testing**: You can select `localhost:8080` if you only want local testing on the host machine.

---

## Troubleshooting

<details>
<summary><b>Can I run multiple tunnels simultaneously?</b></summary>

Yes! AutoMorph's dispatcher script and notification system support multiple active endpoints. You can combine profiles:
```dotenv
COMPOSE_PROFILES=cloudflare,pangolin
```
Both tunnel links will appear in your notification emails with dedicated badges, and you can switch between them anytime in **Settings** › **External URL / Access Mode**!
</details>

<details>
<summary><b>Why doesn't a tunnel appear in the Web Settings dropdown?</b></summary>

The dropdown only includes tunnels that are actively configured in your `.env`. If a tunnel's URL or token is missing or left empty, AutoMorph excludes it so only working endpoints are offered. Verify your `.env` settings and restart your container if necessary.
</details>

<details>
<summary><b>Why does Obtainium on my phone say connection refused or fail to download?</b></summary>

If the server is using `localhost:8080`, external devices cannot reach it because `localhost` on a phone refers to the phone itself. Go to the AutoMorph web portal under **Settings** › **External URL / Access Mode**, select your active tunnel or custom URL, and save settings.
</details>

<details>
<summary><b>The tunnel is connected, but the web page returns 502 Bad Gateway.</b></summary>

A 502 indicates the tunnel container cannot reach `automorph:8080`.
* Check if the main AutoMorph container is running: `docker compose ps`
* Inspect AutoMorph logs: `docker compose logs -f automorph`
* Ensure the target hostname in your tunnel config is `automorph` (the Docker service name) and port is `8080`.
</details>

---

[← Previous: Oracle Setup](oracle-setup.md) | [Documentation Hub](README.md) | [Next: User Guide →](user-guide.md)
