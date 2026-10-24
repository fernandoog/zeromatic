# Contribuir

1. Haz un fork o una rama desde `main`.
2. No subas secretos (`email.env`, claves SSH, App Passwords, `OPENAI_API_KEY`).
3. Prueba en hardware real si tocas el daemon o comandos AT (el módem Conexant es quisquilloso con `ATA`/`VLS`).
4. Mantén los scripts de shell en LF; en Windows: `git config core.autocrlf input` en este repo.

## Estilo

- Python 3.11+ compatible con Pi Zero (evitar dependencias pesadas).
- Comentarios solo donde aclaren secuencias AT no obvias.
- Documentación en español.
