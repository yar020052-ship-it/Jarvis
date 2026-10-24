# JARVIS Bootstrapper

## Windows
```powershell
irm https://raw.githubusercontent.com/yar020052-ship-it/Jarvis/main/scripts/bootstrap/setup-jarvis.ps1 | iex
```
или скачать `setup-jarvis.ps1` и запустить.

## Linux/macOS
```bash
curl -sL https://raw.githubusercontent.com/yar020052-ship-it/Jarvis/main/scripts/bootstrap/setup-jarvis.sh | bash
```

Bootstrapper сам определит ОС/архитектуру, скачает нужный релиз под ПК и запустит апдейтер при наличии.
`yar020052-ship-it` замените на свой GitHub-репозиторий после публикации релизов.
