[🏠 Documentation Hub](README.md) › **User Guide & Configuration**

---

# AutoMorph User Guide & Configuration

Welcome to the AutoMorph user guide! This document explains how to use the web interface to configure packages, customize patch options, manage build schedules, and download patched APKs.

---

## Table of Contents

- [1. Web Interface Overview](#1-web-interface-overview)
- [2. Adding & Managing Packages](#2-adding--managing-packages)
  - [2.1 Add Package Wizard](#21-add-package-wizard)
  - [2.2 Choosing Patch Repositories](#22-choosing-patch-repositories)
  - [2.3 Enabling and Disabling Apps](#23-enabling-and-disabling-apps)
- [3. Customizing Patch Options & Patch Aliases](#3-customizing-patch-options--patch-aliases)
  - [3.1 Patch Selection & Options](#31-patch-selection--options)
  - [3.2 Patch Aliases (Cross-Repository Patch Mapping)](#32-patch-aliases-cross-repository-patch-mapping)
- [4. Triggering Builds & Patching Strategies](#4-triggering-builds--patching-strategies)
  - [4.1 Manual Single or Batch Builds](#41-manual-single-or-batch-builds)
  - [4.2 Scheduled Automated Builds](#42-scheduled-automated-builds)
  - [4.3 Patching Strategies & Fallback Flow (Strategy 1 vs. Strategy 2)](#43-patching-strategies--fallback-flow-strategy-1-vs-strategy-2)
- [5. System & Performance Settings](#5-system--performance-settings)
  - [5.1 Low-Spec Mode (Recommended for Free Tier)](#51-low-spec-mode-recommended-for-free-tier)
  - [5.2 Repository Delivery Base URL (Optional)](#52-repository-delivery-base-url-optional)
  - [5.3 Timezone & Schedule Time](#53-timezone--schedule-time)
  - [5.4 Retention & Storage Cleanup](#54-retention--storage-cleanup)
- [6. Custom Keystore & Seamless Updates (Morphe App Compatibility)](#6-custom-keystore--seamless-updates-morphe-app-compatibility)
  - [6.1 Why Keystores Matter for Updates](#61-why-keystores-matter-for-updates)
  - [6.2 Exporting and Placing Your Keystore](#62-exporting-and-placing-your-keystore)
- [7. Email Dispatcher & Notifications](#7-email-dispatcher--notifications)
- [8. Downloading Builds](#8-downloading-builds)
- [9. (Optional) Obtainium & F-Droid Sync](#9-optional-obtainium--f-droid-sync)
  - [9.1 How It Works](#91-how-it-works)
  - [9.2 1-Tap Import via Obtainium (QR Code & Deep Link)](#92-1-tap-import-via-obtainium-qr-code--deep-link)
  - [9.3 Adding AutoMorph as an F-Droid Repository (Obtainium & Neo Store)](#93-adding-automorph-as-an-f-droid-repository-obtainium--neo-store)
  - [9.4 Seamless In-Place App Updates](#94-seamless-in-place-app-updates)

---

## 1. Web Interface Overview

Once you navigate to your AutoMorph portal (via your [configured tunnel](tunnels.md) or local address), the dashboard displays:
* **System Status & Stats**: Total tracked packages, last build timestamp, and next scheduled run.
* **Tracked Applications List**: Cards showing version numbers, current patch status, and download links.
* **Console & Real-time Logs**: Live build status streamed directly to your browser when a build is underway.

---

## 2. Adding & Managing Packages

### 2.1 Add Package Wizard
1. On the dashboard, click **Add Package** (or **+**).
2. Enter the package name (e.g. `com.reddit.frontpage` or `com.google.android.youtube`).
3. AutoMorph automatically checks Google Play and fallback sources (APKCombo, Uptodown, APKPure) to detect valid versions and metadata.

### 2.2 Choosing Patch Repositories
Specify which patch repository to use for the app (e.g., ReVanced, Morphe patches, or custom community repositories). AutoMorph queries the repository releases and discovers compatible patches.

### 2.3 Enabling and Disabling Apps
Each app card features a toggle switch:
* **Enabled (ON)**: Included in daily scheduled patching checks.
* **Disabled (OFF)**: Kept in your configuration but skipped during automated runs.

---

## 3. Customizing Patch Options & Patch Aliases

For any package, click **Options** / **Customize Patches** to open the package configuration modal.

### 3.1 Patch Selection & Options
* **Explore Patches**: View all available patches provided by your configured repositories.
* **Toggle Patches**: Switch individual patches ON or OFF (e.g., enable custom branding, remove ads, or disable debugging).
* **Configure Values**: Edit patch option values where supported (e.g., custom package names, user-agent overrides).
* **Save Changes**: Click **Save Options** to persist your selections.

### 3.2 Patch Aliases (Cross-Repository Patch Mapping)
When you enable patches from two or more repositories (for instance, the official Morphe patches and community or ReVanced repositories), different repositories often contain patches that perform the exact same role under different names (e.g., `Unlock Premium` vs. `Enable Premium`, or `Hide Ads` vs. `Ad-blocker`).

Without patch aliases, having both patches enabled could cause collisions or patch failures when switching between versions.

#### How Patch Aliases Work:
1. **Define an Alias Name**: Create a new alias and give it any name you like (e.g., `Unlock Premium`). *The only restriction is that the alias name must not match the exact name of another already enabled patch.*
2. **Assign Patches per Repository**: Use the dropdown menus for each configured repository to select which patch fulfills that alias role in that specific repository.
3. **Dynamic Version-Aware Selection**: During build time, AutoMorph inspects the package version currently being targeted:
   * If a repository's aliased patch is not compatible with the target version, AutoMorph automatically disables it in that repository's options.
   * If a repository's aliased patch matches the target version, AutoMorph automatically enables it.

> [!TIP]
> Patch aliases prevent conflicts when combining multiple repositories and ensure that AutoMorph always picks the right patch for the specific version being patched without any manual intervention.

---

## 4. Triggering Builds & Patching Strategies

### 4.1 Manual Single or Batch Builds
* **Single App**: Click the **Run** button on an individual app card to trigger patching for just that package.
* **Run All**: Click **Start Build** in the top navigation bar to run through all enabled packages.
* During patching, a slide-out console displays progress output in real time.

### 4.2 Scheduled Automated Builds
AutoMorph includes an internal daily scheduler. It runs automatically once every 24 hours at the time configured in your system settings.

### 4.3 Patching Strategies & Fallback Flow (Strategy 1 vs. Strategy 2)
When AutoMorph begins patching a package, it collects the supported package versions across all enabled patches and sorts them from highest to lowest. It then runs through an automated two-stage patching pipeline:

```mermaid
graph TD
    A[Start Patching Package] --> B[Collect Supported Versions<br/>Highest to Lowest]
    B --> C[Strategy 1: Attempt All Enabled Patches]
    C -->|Try Highest Version| D{Patch Succeeded?}
    D -->|Yes| E[Build Successful! Save APK]
    D -->|No| F{More Versions Available?}
    F -->|Yes| G[Step Down to Next Lower Version] --> C
    F -->|No| H[Strategy 2: Version-Specific Patches Only]
    H -->|Try Highest Version| I{Patch Succeeded?}
    I -->|Yes| E
    I -->|No| J{More Versions Available?}
    J -->|Yes| K[Step Down to Next Lower Version] --> H
    J -->|No| L[Build Failed: Log Errors]
```

#### Strategy 1: All Enabled Patches (Force Mode)
* **How it works**: AutoMorph attempts to apply **all enabled patches** to the highest listed supported package version (running Morphe CLI with `--force`).
* **Fallback**: If patching fails (for example, if a newly released app version changed bytecode that a particular patch cannot yet handle), AutoMorph does not immediately give up. It moves down the version list, attempting to patch the next lower supported version with all enabled patches.
* **Goal**: Maximizes feature coverage by trying to give you every patch you asked for on the most modern version possible.

#### Strategy 2: Version-Specific Patches Only (Strict Compatibility Mode)
* **When it activates**: If all supported versions fail under Strategy 1, AutoMorph skips to Strategy 2.
* **How it works**: AutoMorph resets back to the top of the version list, but this time **only enables patches that are explicitly declared compatible with that exact version** (plus universal patches with no version restrictions). Any patch not certified for that version is excluded.
* AutoMorph works down the version list under Strategy 2 until it finds a combination that builds cleanly.
* **Goal**: Guarantees you still get a working, stably patched application even if certain cutting-edge patches are not compatible with newer versions.

---

## 5. System & Performance Settings

Access the **Settings** tab in the top navigation bar to fine-tune your environment:

### 5.1 Low-Spec Mode (Recommended for Free Tier)
* **What it does**: Optimizes memory allocation and builds packages sequentially with garbage collection between steps.
* **When to enable**: Essential when hosting on an [Oracle Free Tier E2.1.Micro instance](oracle-setup.md) with 1 GB RAM to prevent out-of-memory errors.

### 5.2 Repository Delivery Base URL (Optional)
* **What is this for?**: Sets the base URL embedded in your Obtainium configuration and F-Droid repository index for APK downloads.
* **Automatic Detection**: AutoMorph automatically detects your active tunnels from `.env` (Pangolin, Cloudflare, or Tailscale) and assigns a default address.
* **When to adjust this**:
  * If you run multiple tunnels simultaneously (e.g. both Cloudflare and Pangolin) and want to choose which one your phone uses for downloads.
  * If you do not use an automated tunnel and want to enter your own custom domain or LAN IP.
  * If you want to use local mode (`localhost:8080`) for local testing.
* **The Options Dropdown**:
  * Only lists tunnels that are actively configured in `.env` (unconfigured tunnels are omitted).
  * Choose **Custom Outside URL** to enter a manual domain or LAN IP.

### 5.3 Timezone & Schedule Time
* **Schedule Time**: Specify when daily runs should execute (e.g., `03:00` for 3:00 AM).
* **Timezone**: Set your local IANA timezone (e.g., `America/New_York`, `Europe/London`, `Asia/Tokyo`) so schedules match your local time.

### 5.4 Retention & Storage Cleanup
To prevent your storage disk from filling up:
* **Max Original Packages**: Number of downloaded source APKs to keep per app before pruning old versions.
* **Max Patched Packages**: Number of completed patched APKs to retain per app.
* **Max Logs**: Number of historical build log files to keep.

---

## 6. Custom Keystore & Seamless Updates (Morphe App Compatibility)

AutoMorph signs all patched APKs so they can be installed directly on Android devices. To make updates as smooth as possible, you can provide your own signing keystore.

### 6.1 Why Keystores Matter for Updates
On Android, an existing app can only be updated if the new APK is signed with the **exact same cryptographic key and certificate** as the currently installed version.

If an update is signed with a different key, Android will refuse the installation with a signature mismatch error (e.g., *“App not installed as package appears to be invalid”*). To install it, you would have to uninstall the app, wiping all user data and settings.

By sharing a keystore:
* APKs built by AutoMorph can be installed directly over apps patched on your phone using the Morphe app.
* Apps patched by the Morphe app can be updated directly over APKs built by AutoMorph.
* Builds can upgrade over older AutoMorph installations seamlessly.

### 6.2 Exporting and Placing Your Keystore

AutoMorph's patching engine looks for keystore files in the Morphe data root directory: `tools/morphe-data/`.

Because `./tools/morphe-data` is mounted to `/automorph/tools/morphe-data:rw` in `compose.yaml`, files placed in this folder on your host machine are immediately available to the patcher and persist across container updates.

#### Step-by-Step Instructions:
1. **Export from the Morphe Android App** (if using the mobile app):
   * Open the **Morphe app** on your Android device.
   * Navigate to **Settings** › **Keystore**.
   * Tap **Export keystore** and save the file (e.g., `morphe.keystore`).
2. **Place the File in AutoMorph**:
   * Copy your exported keystore file into the `tools/morphe-data/` folder on your AutoMorph host:
     ```bash
     cp /path/to/exported.keystore ./tools/morphe-data/morphe.keystore
     ```
   * Ensure the file is named `morphe.keystore` (or `Morphe.keystore`).
3. **Automatic Detection**:
   * AutoMorph will automatically use this keystore for all subsequent builds. No restart or container rebuild is required!

> [!NOTE]
> If you do not provide a custom keystore, AutoMorph will automatically generate a default keystore in `tools/morphe-data/` upon its first build and continue reusing it for all future builds.

---

## 7. Email Dispatcher & Notifications

AutoMorph can send rich email build reports directly to your inbox whenever patching completes.

1. Ensure `SMTP_USERNAME` and `SMTP_PASSWORD` are defined in your `.env` file (see [Oracle Setup](oracle-setup.md#32-configure-env)).
2. In the web portal, launch the **Email Settings Wizard** under Settings.
3. Configure:
   * **Sender / Recipient Email**: Address to deliver notifications to.
   * **SMTP Host & Port**: (e.g., `smtp.gmail.com` on port `587`).
4. Click **Send Test Email** to verify your configuration.

Each report contains direct download links, checksums, and patch summaries.

---

## 8. Downloading Builds

You can download completed APKs through several convenient methods:
1. **Web Dashboard**: Click the **Download** button directly on any completed app card.
2. **Notification Emails**: Click the branded download button corresponding to your active tunnel ([Pangolin, Tailscale, or Cloudflare](tunnels.md)).
3. **Mounted Host Volumes**: Completed APKs are stored directly in `./apps/patched/` on the host machine.
4. **(Optional) Automated App Managers**: If you want your phone to automatically pull updates in the background, you can optionally connect [Obtainium or F-Droid](#9-optional-obtainium--f-droid-sync).

---

## 9. (Optional) Obtainium & F-Droid Sync

> [!NOTE]
> **This feature is completely optional.**
> You can always download APKs directly from the dashboard or through your email notifications. You only need this section if you use the [Obtainium](https://github.com/ImranR98/Obtainium) Android app or an F-Droid client and want hands-free, automated app updates delivered directly to your device.

If you use Obtainium or an F-Droid client (like F-Droid, Neo Store, or Droid-ify), AutoMorph can act as a personal app repository. It automatically generates:
1. An **F-Droid v2 Repository Index** (`/repo/index-v2.json`)
2. An **Obtainium Configuration Export** (`/automorph-obtainium.json`)

### 9.1 How It Works

When AutoMorph finishes building your apps, it updates these live feeds using your server's active address. Your phone's Obtainium app can periodically check this feed in the background:

```mermaid
sequenceDiagram
    autonumber
    participant AutoMorph as AutoMorph Server
    participant Feed as F-Droid Feed (/repo/index-v2.json)
    participant Phone as Android Phone (Obtainium)

    AutoMorph->>AutoMorph: Daily Patching Completes
    AutoMorph->>Feed: Updates index-v2.json & automorph-obtainium.json
    Note over Phone,Feed: Scheduled background check
    Phone->>Feed: Queries latest version & metadata
    Feed-->>Phone: Returns new APK version & download URL
    Phone->>Phone: Alerts user / Installs APK update!
```

### 9.2 1-Tap Import via Obtainium (QR Code & Deep Link)

If you'd like to import all your patched apps into Obtainium at once:

1. Click the **Obtainium Sync** button in the top navigation or on the dashboard.
2. Choose how to connect:
   * **From Desktop (Scan QR Code)**: Point your phone camera or barcode scanner at the QR code. It opens an `obtainium://` deep link that prompts Obtainium to add all your apps in one tap.
   * **From Mobile (1-Tap Direct Link)**: If you are viewing the dashboard on your phone, tap **"1-Tap Add All to Obtainium"**.
   * **Manual JSON File Import**: Click **Download JSON File** to save `automorph-obtainium.json` and import it in Obtainium via **Settings** › **Import / Export** › **Import** › **From JSON file**.

> [!TIP]
> If your server is running on `localhost:8080` without any tunnel configured, mobile devices won't be able to reach download links. Simply select your tunnel or LAN URL under **Settings** › **External URL / Access Mode**.

### 9.3 Adding AutoMorph as an F-Droid Repository (Obtainium & Neo Store)

You can also add AutoMorph as an F-Droid repository feed directly in **Obtainium** or **Neo Store**:

> [!NOTE]
> Currently, the generated F-Droid repository index is unsigned. It works directly with **Obtainium** and **Neo Store** (which support unsigned repositories). Official F-Droid and Droid-ify require signed repository certificates, which will be implemented in a future update.

1. In the Obtainium sync modal, click **Copy F-Droid Repo URL** (`https://<your-url>/repo`).
2. In your mobile client:
   * **Obtainium**: Tap **Add App** › paste your repo URL as an F-Droid source.
   * **Neo Store**: Go to **Settings** › **Repositories** › tap **+** (Add) and paste the URL.

### 9.4 Seamless In-Place App Updates

When paired with a **Custom Keystore** (see [Section 6](#6-custom-keystore--seamless-updates-morphe-app-compatibility)), all updates share identical signatures. When Obtainium detects a new release, Android can update the existing app seamlessly without uninstalling or losing your data.

---

[← Previous: Remote Access & Tunnels Guide](tunnels.md) | [Documentation Hub](README.md)
