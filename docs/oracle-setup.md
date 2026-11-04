[🏠 Documentation Hub](README.md) › **Oracle Cloud Free Tier Setup**

---

# Setting Up Oracle Free Tier VPS & Launching AutoMorph (Optional)

> [!TIP]
> **Have your own server already?** AutoMorph runs anywhere Docker is supported (home server, homelab, Unraid, TrueNAS, Raspberry Pi, or another VPS). If you already have hardware to run it on, you can skip this guide entirely and head straight to the [Remote Access & Tunnels Guide](tunnels.md).

This guide walks you through creating and launching your own free Virtual Private Server (VPS) on Oracle Cloud Infrastructure (OCI) under their **Always Free** tier, configuring SSH access, and starting AutoMorph.

> [!NOTE]
> Oracle can and does change their policies from time to time, so keep an eye out to make sure your service does not creep into a paid tier. As long as you stay on the pure Always Free tier and do not convert to Pay-As-You-Go (which can also be free), you should not accidentally incur a charge. You will need a card to register; Oracle charges a temporary verification hold (~$1.00 USD) that is returned to confirm your account is genuine and that you are of age. Note that the location of your server cannot be changed once set on the free tier.

---

## Table of Contents

- [Step 1: Create a New Instance](#step-1-create-a-new-instance)
  - [1.1 Open Instance Creation](#11-open-instance-creation)
  - [1.2 Basic Instance Settings & Shape](#12-basic-instance-settings--shape)
  - [1.3 Networking](#13-networking)
  - [1.4 Storage & Boot Volume](#14-storage--boot-volume)
  - [1.5 Review & Launch](#15-review--launch)
- [Step 2: Entering the Server (SSH)](#step-2-entering-the-server-ssh)
  - [2.1 SSH via Windows PowerShell](#21-ssh-via-windows-powershell)
  - [2.2 Windows SSH Key Permission Fix (If Needed)](#22-windows-ssh-key-permission-fix-if-needed)
  - [2.3 SSH via macOS / Linux](#23-ssh-via-macos--linux)
- [Step 3: Starting AutoMorph](#step-3-starting-automorph)
  - [3.1 Enter the Project Folder](#31-enter-the-project-folder)
  - [3.2 Configure `.env`](#32-configure-env)
  - [3.3 Starting the Service](#33-starting-the-service)
  - [3.4 Access the Web Portal](#34-access-the-web-portal)
- [Troubleshooting & FAQs](#troubleshooting--faqs)

---

## Step 1: Create a New Instance

### 1.1 Open Instance Creation
1. Log into your [Oracle Cloud Console](https://cloud.oracle.com/).
2. Click the hamburger menu icon (☰) in the top-left corner and navigate to:
   ```text
   Compute > Instances > Create Instance
   ```

<!-- Screenshot placeholder: Oracle menu showing Compute > Instances -->

---

### 1.2 Basic Instance Settings & Shape

* **Name**: Give your instance a memorable name (e.g., `AutoMorph`).
* **Compartment and Placement**: Leave these at their default settings.
* **Image**:
  * Click **Change image**.
  * Select **Canonical Ubuntu** > **Canonical Ubuntu 24.04 Minimal** (or Ubuntu 22.04 Minimal).
  
<!-- Screenshot placeholder: Selecting Ubuntu Minimal -->

* **Shape**:
  * Make sure the shape selected is **VM.Standard.E2.1.Micro** with the **Always Free-eligible** badge.
  * If it defaults to something else, click **Change shape**, select **Specialty and legacy**, and pick **VM.Standard.E2.1.Micro**.

<!-- Screenshot placeholder: Shape selection VM.Standard.E2.1.Micro -->

#### Automated Setup (Initialization Script):
To automatically install Docker, set up a 4GB swapfile, optimize networking MTU, and download the AutoMorph templates:
1. Scroll down and expand **Advanced Options**.
2. Click the **Management** tab.
3. Under **Cloud-init script**, choose **Upload cloud-init script**.
4. Select the [`oracle-cloud-init.yaml`](../oracle-cloud-init.yaml) file from this repository.

<!-- Screenshot placeholder: Uploading the initialization script in Management tab -->

> [!TIP]
> Using `oracle-cloud-init.yaml` automates Docker installation, kernel BBR tuning, MTU configuration, and sets up 4GB of swap space to ensure smooth patching performance on the 1GB RAM micro instance.

---

### 1.3 Networking

Under the **Networking** section:
* **Primary network**: Keep **Create new virtual cloud network** and **Create new public subnet** selected.
* **Assign a public IPv4 address**: Ensure this is checked (**Yes**). If you do not assign a public IP, you will not be able to connect over the internet.
* **SSH Keys**:
  * Choose **Generate a key pair for me**.
  * Download **both** your private key (`.key`) and public key (`.pub`).
  
> [!CAUTION]
> Store your downloaded `.key` file in a secure location. You cannot redownload this key after leaving this page!

<!-- Screenshot placeholder: Downloading SSH keys -->

---

### 1.4 Storage & Boot Volume

An Oracle Free Tier account currently is permitted a maximum of **200 GB** in storage across all their instances.

If desired, you may edit the boot volume size (default is 50 GB) or create and mount an attached volume that can be shared between other instances. An attached volume can be created and mounted at a later date.

For this guide, we will assume that you just use the boot volume. If you wish to increase its size now, tick the **Specify a custom boot volume size and performance** setting and specify your desired boot volume size (up to 200 GB).

<!-- Screenshot placeholder: Custom boot volume size setting -->

---

### 1.5 Review & Launch

1. Review your settings and click **Create**.
2. The instance status will show **PROVISIONING** (orange) and switch to **RUNNING** (green) within a minute or two.
3. Note down the **Public IP Address** displayed on the instance details page.

> [!NOTE]
> If you provided the initialization script (`oracle-cloud-init.yaml`), allow an additional **3 to 5 minutes** after the instance turns green so background package updates and Docker installation can finish.

---

## Step 2: Entering the Server (SSH)

### 2.1 SSH via Windows PowerShell

Open Windows PowerShell and use the standard OpenSSH command:

```powershell
ssh -i <PATH-TO-KEY> ubuntu@<PUBLIC-IP>
```

**Parameters**:
* `<PATH-TO-KEY>`: Shift + Right-click your downloaded `.key` file in File Explorer and select **Copy as path**.
* `ubuntu`: The default username for Ubuntu instances in Oracle Cloud.
* `<PUBLIC-IP>`: The public IP address from your instance dashboard.

**Example**:
```powershell
ssh -i "C:\Users\YourName\Downloads\ssh-key.key" ubuntu@129.150.45.12
```
When prompted `Are you sure you want to continue connecting (yes/no/[fingerprint])?`, type `yes` and hit **Enter**.

---

### 2.2 Windows SSH Key Permission Fix (If Needed)

If Windows PowerShell displays an error like:
```text
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@         WARNING: UNPROTECTED PRIVATE KEY FILE!          @
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
Permissions for 'ssh-key.key' are too open.
```

Fix the file permissions directly in PowerShell by running:
```powershell
# Replace with the actual path to your key
$KeyPath = "C:\Users\YourName\Downloads\ssh-key.key"

icacls $KeyPath /inheritance:r
icacls $KeyPath /grant:r "$($env:USERNAME):(R)"
```

---

### 2.3 SSH via macOS / Linux

On macOS or Linux, restrict the key permissions before connecting:
```bash
chmod 600 /path/to/ssh-key.key
ssh -i /path/to/ssh-key.key ubuntu@<PUBLIC-IP>
```

---

## Step 3: Starting AutoMorph

### 3.1 Enter the Project Folder

If you used the initialization script, the directory is already created with the required templates:
```bash
cd automorph
```

*(If you did not use the initialization script, install Docker & Docker Compose first, then run `mkdir automorph && cd automorph` and download `default_compose.yaml` as `compose.yaml` and `example.env` as `.env`).*

You can verify whether the initialization script has completed by running:
```bash
tail -f /var/log/cloud-init-output.log
```
*(Press `Ctrl + C` once you see completion).*

---

### 3.2 Configure `.env`

> [!IMPORTANT]
> **Set up your tunnel first!** Before editing `.env`, choose and configure your preferred remote access tunnel (see the [Remote Access & Tunnels Guide](tunnels.md)). You will need the public tunnel URL or tokens generated from that step to paste into your `.env`.

Edit the `.env` file to set up email reporting, downloads, and remote access tunnels:
```bash
nano .env
```

Key configuration areas:
1. **Email Reports**: Enter your `SMTP_USERNAME` and `SMTP_PASSWORD` (for Gmail, generate a 16-character [Google App Password](https://myaccount.google.com/apppasswords)).
2. **Tunnel & Remote Access**: Paste the credentials from your chosen tunnel provider ([Pangolin, Tailscale, or Cloudflare](tunnels.md)):
   * **Pangolin**: Fill in `PANGOLIN_ENDPOINT`, `PANGOLIN_ID`, `PANGOLIN_SECRET`, and `PANGOLIN_URL`.
   * **Tailscale Funnel**: Fill in `TAILSCALE_FUNNEL_URL`.
   * **Cloudflare Tunnel**: Fill in `CLOUDFLARE_TUNNEL_TOKEN` and `CLOUDFLARE_TUNNEL_URL`.
3. **Set Compose Profile**: If using Pangolin or Cloudflare, ensure you set:
   ```bash
   COMPOSE_PROFILES=pangolin
   # or COMPOSE_PROFILES=cloudflare
   ```
   *(Tailscale runs directly on the host, so it does not need a compose profile).*
4. **Google Play Authentication (Recommended for Oracle)**:
   Google frequently blocks or rate-limits anonymous Aurora Store dispenser tokens on Oracle Cloud IP ranges. If you want reliable Google Play downloads on your Oracle VPS, configure a dedicated, disposable Google account:
   * `GOOPDL_ACCOUNT_EMAIL`: Your disposable Google account email.
   * `GOOPDL_AAS_TOKEN`: Your generated AAS token (see the [apkeep Google Play Guide](https://github.com/EFForg/apkeep/blob/master/USAGE-google-play.md) for how to generate this).
   > [!WARNING]
   > Always use a secondary, disposable Google account for this purpose — never your primary personal account.

To save and exit in `nano`:
* Press `Ctrl + O`, then `Enter` to save.
* Press `Ctrl + X` to exit.

Verify your saved values:
```bash
cat .env
```

---

### 3.3 Starting the Service

1. Pull the latest AutoMorph image:
   ```bash
   docker compose pull
   ```
2. Start the application in the background:
   ```bash
   docker compose up -d
   ```
3. Check container status:
   ```bash
   docker compose ps
   ```

<!-- Screenshot placeholder: docker compose up -d output -->

---

### 3.4 Access the Web Portal

Once the containers are running:
* If you set up **Pangolin** or **Cloudflare**, navigate to your configured tunnel URL in your browser.
* If you set up **Tailscale**, navigate to your MagicDNS name or Funnel URL.

Congratulations! Your AutoMorph instance is running on Oracle's free tier.

---

## Troubleshooting & FAQs

<details>
<summary><b>Why can't I access http://&lt;PUBLIC-IP&gt;:8080 directly in my browser?</b></summary>

Oracle Cloud Virtual Cloud Networks (VCNs) and Ubuntu's default firewall block ingress traffic on arbitrary ports like 8080 by default. Rather than exposing raw ports over plain HTTP, we strongly recommend using a tunnel (Pangolin, Tailscale, or Cloudflare). Tunnels provide free SSL encryption and require no open ports on the server.
</details>

<details>
<summary><b>How do I check live container logs?</b></summary>

To inspect logs:
```bash
# AutoMorph main application logs
docker compose logs -f automorph

# Pangolin tunnel logs
docker compose logs -f pangolin

# Cloudflare tunnel logs
docker compose logs -f cloudflared
```
</details>

<details>
<summary><b>Will my Oracle instance stay free forever?</b></summary>

Under Oracle's current Always Free policies, standard shapes like `VM.Standard.E2.1.Micro` and up to 200 GB total storage incur no charges. However, **Oracle can and does change their policies and terms of service from time to time**. You should keep an eye on your account and Oracle email announcements to make sure your service stays within the free tier. As long as you stay on the pure free tier and not pay-as-you-go, you should not accidentally incur a charge.
</details>

---

[← Documentation Hub](README.md) | [Next: Remote Access & Tunnels Guide →](tunnels.md)
