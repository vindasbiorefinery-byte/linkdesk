# LinkDesk — free remote control, no server needed

Control a computer or Android phone from **any device with a browser**
(Android phone, iPhone, iPad, tablet, Windows, Mac, Linux, Chromebook).
Everything is free: no server to rent, no account to create, no card.

| Device | Control others (web page) | Be controlled (free app) |
|---|---|---|
| Windows / Mac / Linux | ✅ | ✅ |
| Android phone or tablet | ✅ | ✅ |
| iPhone / iPad | ✅ | ❌ Apple doesn't allow it for any app |

**Features:** 9-digit ID · Accept/Decline prompt · live screen · mouse, touch and
keyboard control · Back/Home/Apps buttons for Android · chat · file transfer
both ways · unattended password for computers · encrypted direct connection.

## How it works

```
 Controller (any browser)                         Controlled device (LinkDesk app)
        │                                                     │
        └──► free PeerJS service: "where is 482 913 775?" ◄───┘   (introduction only)
        │                                                     │
        └═══════ direct, encrypted WebRTC connection ═════════┘   (screen, input, chat, files)
```

The free public [PeerJS](https://peerjs.com) service only introduces the two
devices. The screen, mouse, keyboard, chat and files travel directly between
them, encrypted. GitHub hosts the web page (GitHub Pages) and builds the apps
(GitHub Actions), both free.

## Project layout

```
docs/                     web controller (served free by GitHub Pages)
app/linkdesk.py, core.py  Windows / Mac / Linux app (Python)
android/                  Android app (Java + WebView)
.github/workflows/build.yml   builds all apps and publishes a release
```

## Setup (about 15 minutes, all in the browser except step 2)

1. Create a free account at github.com and a new **public** repository named `linkdesk`.
2. Upload this folder:
   ```
   cd linkdesk
   git init
   git add .
   git commit -m "LinkDesk v2"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/linkdesk.git
   git push -u origin main
   ```
3. **Turn on the web page:** repository **Settings → Pages** → Source *Deploy from a branch* →
   Branch `main`, folder `/docs` → **Save**. After a minute your page is at
   `https://YOUR-USERNAME.github.io/linkdesk/`.
4. **Build the apps:** **Actions** tab → **Build LinkDesk** → **Run workflow**.
   After about 10 minutes the **Releases** page has
   `LinkDesk-Windows.exe`, `LinkDesk-Mac.zip`, `LinkDesk-Linux`, `LinkDesk-Android.apk`.
5. Install the app on each device that should be controllable. It shows a 9-digit ID.
6. To control it, open your web page on any device, type the ID, tap **Connect**.

## First run on each device

| Device | What to do once |
|---|---|
| Windows | "Windows protected your PC" → **More info → Run anyway** (the app isn't code-signed). |
| Mac | Unzip, right-click LinkDesk → **Open**. Then System Settings → Privacy & Security → allow **Screen Recording** and **Accessibility** for LinkDesk, and reopen it. |
| Linux | `chmod +x LinkDesk-Linux && ./LinkDesk-Linux`. Use an Xorg session (Wayland blocks screen capture and input). |
| Android | Allow "Install unknown apps" for your browser. In LinkDesk tap **Turn on remote control** → Accessibility → LinkDesk → On. On every session Android also asks you to allow screen sharing; tap **Start now**. |

## Using it

- **Phone or tablet as controller:** tap = click, drag = drag/swipe, long-press = right-click
  (long-press on Android), two-finger drag = scroll, **⌨** opens the keyboard.
- **Computer as controller:** mouse and keyboard work as usual; the wheel scrolls.
- Received files go to `Downloads/LinkDesk`.
- **Unattended access (computers):** click **Set password…** in the app, then enter that
  password on the web page with the ID; no Accept click needed. Five wrong passwords in
  10 minutes turns password login off for 10 minutes. Android always asks the person,
  because Android requires approval for every screen share.

## Good to know

- **Some networks block direct connections** (strict office firewalls, some mobile
  data networks). When that happens the connection times out; switching one side
  to home Wi-Fi usually fixes it. Getting through every network needs a paid relay
  ("TURN server"), which LinkDesk doesn't use.
- The PeerJS service is a free community service with no guarantee. If it is ever down,
  you can run your own copy (`npm i peer && npx peerjs --port 9000`) and add
  `?signal=your-host:9000` to the web page address.
- The Android app uses a shared signing key (`android/app/linkdesk.keystore`, password
  `linkdesk`) so updates install over old versions. For a public release, replace it with
  your own private key.
- Android and Windows may show warnings for apps installed from outside the stores.
- Shares the main screen only; no sound or clipboard sync yet.

Use LinkDesk only on devices you own or have permission to access.
