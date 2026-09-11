# Deploy

```bash
cp deploy/poly-bot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now poly-bot
```

`.env` debe tener permisos `600` (`chmod 600 /opt/poly-bot-4040/.env`) y pertenecer a `polybot:polybot`.
