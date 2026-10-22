# Deploying Menu Watch to demo.superagentk.com

After this the dashboard is at **https://demo.superagentk.com/menuwatch/** and requires a login. It runs the demo footage on a loop for now.

## 1. Build the upload file (on your PC)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\make_package.ps1
```

This creates `menuwatch-deploy.tar.gz` (about 15 MB) in the project folder.

## 2. Upload it to the server

Use the same user and key you normally use to SSH into the server:

```powershell
scp -i path\to\your-key.pem menuwatch-deploy.tar.gz ubuntu@SERVER-IP:~
```

(On Amazon Linux the user is `ec2-user` instead of `ubuntu`.)

## 3. Install it (on the server)

```bash
ssh -i path/to/your-key.pem ubuntu@SERVER-IP
tar xzf menuwatch-deploy.tar.gz
sudo bash menuwatch/deploy/install.sh
```

The first run downloads about 1 GB of libraries (mostly PyTorch). On AWS this usually takes 5–10 minutes. At the end it prints the URL, the username (`admin`) and a **new password**. Save the password somewhere safe.

What the installer does:

| Step | Where |
|---|---|
| App code, model and demo footage | `/opt/menuwatch` |
| Python libraries (CPU-only PyTorch, about 1.5 GB) | `/opt/menuwatch/venv` |
| Database and zone settings (kept across updates) | `/var/lib/menuwatch` |
| Settings and password (readable by root only) | `/etc/menuwatch.env` |
| Background service, restarts automatically | `systemd` unit `menuwatch` |
| Adds `/menuwatch/` to the existing `demo.superagentk.com` nginx site, with a backup and a config test first | `/etc/nginx/snippets/menuwatch.conf` |

Nothing else on the server is changed. The app only listens on `127.0.0.1:8050`, so the outside world reaches it only through nginx. You don't need to open any extra AWS ports.

## Updating later

Repeat steps 1–3. Re-running the installer replaces the code but keeps your data, zones and password.

## Day-to-day commands (on the server)

```bash
sudo systemctl status menuwatch          # is it running?
sudo journalctl -u menuwatch -f          # live log (shows each person counted)
sudo systemctl restart menuwatch         # after editing settings
sudo nano /etc/menuwatch.env             # change password / video source
```

## Server load

- **Demo mode:** the video analysis **pauses when nobody has had the dashboard open for 60 s**, so it uses almost no CPU when idle. It resumes as soon as someone opens the page.
- **When running (tested on a simulated 2-core server):** it uses about 1 CPU core and about 450 MB of RAM, analysing about 4–5 frames per second. Your website stayed fast during the test. The app runs at low priority (`Nice=10`) with a 2 GB memory cap. The installer sizes the CPU threads to the server automatically.
- **Burstable instances:** if the server is a `t3`/`t4g` or Lightsail instance, these earn "CPU credits". Running the analysis 24/7 with a real camera would use them up and slow the whole server down. For a live camera, use a non-burstable type (for example `c7i.large`), or run the analysis at the outlet (see below).

## Connecting a real camera later

The AWS server can't see cameras on the shop's local network directly. Options:

1. **Tailscale VPN (recommended):** install Tailscale on the server and on one always-on device at the outlet (with subnet routing enabled). Then set this in `/etc/menuwatch.env`:
   ```
   MENUWATCH_SOURCE=rtsp://user:pass@192.168.1.64:554/Streaming/Channels/102
   ```
   Remove `MENUWATCH_LOOP` and `MENUWATCH_IDLE_PAUSE_SEC`, then restart the service.
2. **Analyse at the outlet:** a mini PC in the shop runs the analysis and only sends the counts to this server. This needs a small code change.

## If your site isn't served by nginx

The installer prints a warning if something else (Caddy, Apache, a Docker container) is on port 80. Point `/menuwatch/` at `http://127.0.0.1:8050/` with buffering turned off:

**Caddy**
```
handle_path /menuwatch/* {
    reverse_proxy 127.0.0.1:8050 {
        flush_interval -1
    }
}
redir /menuwatch /menuwatch/
```

**Apache** (needs `a2enmod proxy proxy_http`)
```
ProxyPass        /menuwatch/ http://127.0.0.1:8050/ flushpackets=on
ProxyPassReverse /menuwatch/ http://127.0.0.1:8050/
```

## Uninstall

```bash
sudo systemctl disable --now menuwatch
sudo rm -rf /opt/menuwatch /var/lib/menuwatch /etc/menuwatch.env /etc/systemd/system/menuwatch.service
sudo sed -i '/snippets\/menuwatch.conf/d' $(grep -Rl 'snippets/menuwatch.conf' /etc/nginx) && sudo rm /etc/nginx/snippets/menuwatch.conf
sudo nginx -t && sudo systemctl reload nginx
```
