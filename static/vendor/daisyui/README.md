# Notification form components

Locally compiled daisyUI 5.7.50 components (MIT), with Tailwind CLI 4.3.3 (MIT).
Only button, fieldset, input, select and toggle classes are included, using the `nt-` prefix.
The bundle has no Tailwind preflight or daisyUI global theme. Site colors are mapped in `static/team-notifications.css`.

Rebuild from the project root with PowerShell:

```powershell
npm install --prefix .local/notification-ui --no-save tailwindcss@4.3.3 @tailwindcss/cli@4.3.3 daisyui@5.7.50
Copy-Item static/vendor/daisyui/notification-components.source.css .local/notification-ui/input.css
.local/notification-ui/node_modules/.bin/tailwindcss.cmd -i .local/notification-ui/input.css -o static/vendor/daisyui/notification-components.css --minify
```
