# Deploying to Railway

The app runs as one Streamlit web service built from the `Dockerfile`. The image includes Python 3.12,
LibreOffice (for legacy `.ppt` files and PowerPoint thumbnails), and Tesseract (for OCR). Railway injects
`$PORT` and the secrets at runtime, so **no credential is baked into the image or stored in the
repository**. `.env` is excluded by `.gitignore`, `.railwayignore` and `.dockerignore`.

## 1. Get an API key and test it locally

1. Create a key at <https://console.anthropic.com/settings/keys>. Set a **monthly spend limit** on it under
   Console → Settings → Limits.
2. Copy `.env.example` to `.env` and paste the key after `ANTHROPIC_API_KEY=`. Never paste the key into chat,
   email, or a screenshot. If that happens, delete the key in the Console and create a new one.
3. Test the connection:

```powershell
.\.venv\Scripts\python.exe scripts\check_api.py          # one tiny call: key, model, and request settings
.\.venv\Scripts\python.exe scripts\check_api.py --full   # full analysis of the synthetic deck + one-page PDF
```

`--full` writes `samples/live/live_headline_review.pdf`, rendered from the template in
`templates/headline_review_template.json`.

## 2. Variables

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Key from the Claude Console (required) |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` (required) |
| `ANTHROPIC_EFFORT` | `high` |
| `RESEND_API_KEY` | Resend key from <https://resend.com/api-keys> (sending access). It turns on the automated results email. |
| `NOTIFY_TO` | `Info@tencapital.group` (the default; comma-separated for several recipients) |
| `NOTIFY_FROM` | `TEN Capital Headline Tuner <reports@tencapital.group>` (the default). The domain must be verified in Resend. |
| `REVIEW_PREPARED_BY` | Optional "Prepared by" line in the PDF footer |
| `MAX_FILE_MB`, `MAX_SLIDES` | Optional limits (defaults 50 MB / 80 slides) |

Set or rotate secrets without typing them on the command line. Each command reads the value from your local
`.env` file:

```powershell
$rw = "$env:USERPROFILE\.railway\staged-update\railway.exe"
(Select-String .env -Pattern '^ANTHROPIC_API_KEY=(.+)$').Matches[0].Groups[1].Value | & $rw variables --service pitchdeck-headline-tuner --set-from-stdin ANTHROPIC_API_KEY
(Select-String .env -Pattern '^RESEND_API_KEY=(.+)$').Matches[0].Groups[1].Value | & $rw variables --service pitchdeck-headline-tuner --set-from-stdin RESEND_API_KEY
& $rw variables --service pitchdeck-headline-tuner --set "ANTHROPIC_MODEL=claude-opus-5-5" --set "ANTHROPIC_EFFORT=high"
```

Changing a variable redeploys the service.

## 3. First-time setup

```powershell
$rw = "$env:USERPROFILE\.railway\staged-update\railway.exe"
& $rw login                                   # opens the browser
& $rw init --name pitchdeck-headline-tuner
& $rw add --service pitchdeck-headline-tuner
# set the variables (section 2), then:
& $rw up --service pitchdeck-headline-tuner --ci
& $rw domain --service pitchdeck-headline-tuner   # creates the public *.up.railway.app URL
```

## 4. Deploy an update

```powershell
.\.venv\Scripts\python.exe -m pytest            # all tests must pass
& $rw up --service pitchdeck-headline-tuner --ci
& $rw logs --service pitchdeck-headline-tuner   # runtime logs (never contain deck content or keys)
```

The health check is `/_stcore/health`.

## Cost and safety

- **The app has no access password.** Anyone with the URL can run analyses on your Anthropic key, and each
  analysis also sends an email to the configured recipients. Keep a monthly spend limit on the key in the
  Claude Console, and keep an eye on the Resend dashboard.

- Each analysis makes about one model call per 8 slides, plus one narrative pass. A 12-slide deck makes
  about 3 calls. The deck text is prompt-cached across those calls.
- Clicking **Analyze deck** again with the same deck and settings reuses the session's cached result, so
  there is no new API call.
- Uploads are processed in temporary directories that are deleted after extraction. Results live only in
  the user's session memory, and nothing is written to disk or a database.
- The service runs one replica as an unprivileged user. A Streamlit session is lost on redeploy, so users
  should download their PDF, HTML, or JSON before an update.
