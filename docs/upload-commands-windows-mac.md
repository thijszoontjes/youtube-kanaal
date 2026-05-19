# Upload commands voor Windows en Mac

Begin altijd hiermee, zodat het goede Ollama model lokaal staat:

```powershell
ollama pull llama3.2:3b
```

Vervang de datum en tijden in de voorbeelden wanneer nodig. Vandaag is hier als voorbeeld `2026-05-18`.

## Windows PowerShell

Ga eerst naar de projectmap:

```powershell
cd "C:\Users\thijs\OneDrive\Bureaublad\School\youtube-kanaal"
```

Activeer daarna de virtual environment:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### 4 Shorts uploaden met een datum erbij

Deze command maakt 4 Shorts en plant ze op YouTube voor de gekozen datum en tijden:

```powershell
.\.venv\Scripts\python -m youtube_kanaal make-short-schedule --date 2026-05-18 --times "10:00,13:00,15:00,19:00"
```

### Tijd voor de lange video aanpassen

Dit past de uploadtijd alleen aan voor deze PowerShell-sessie:

```powershell
$env:LONG_PUBLISH_TIME="17:00"
```

Wil je het blijvend aanpassen, zet dit in `.env`:

```dotenv
LONG_PUBLISH_TIME=17:00
```

### Vandaag 1 lange video uploaden

Gebruik `--for today` als de lange video vandaag live moet:

```powershell
.\.venv\Scripts\python -m youtube_kanaal generate-and-schedule --for today --upload
```

### Shorts en Reels tegelijk uploaden

Deze command maakt 4 Shorts, plant ze op YouTube, en uploadt dezelfde video's meteen als Instagram Reels:

```powershell
.\.venv\Scripts\python -m youtube_kanaal make-short-schedule-reels --date 2026-05-18 --times "10:00,13:00,15:00,19:00"
```

### Automatisch starten als je Windows laptop aangaat

Dit maakt een Windows Taakplanner-taak die bij aanmelden een PowerShell-terminal opent. Na 2 minuten delay doet hij:

- `ollama pull llama3.2:3b`
- 4 Shorts voor vandaag plannen
- 1 lange video voor vandaag plannen

Eenmalig installeren:

```powershell
.\scripts\install_windows_startup_youtube.ps1 -PublishFor "today" -ShortTimes "10:00,13:00,15:00,19:00" -VideoTime "17:00"
```

Meteen testen zonder opnieuw op te starten:

```powershell
schtasks /Run /TN "youtube-kanaal-startup-upload"
```

Later weer uitzetten:

```powershell
schtasks /Delete /TN "youtube-kanaal-startup-upload" /F
```

## Mac

Ga eerst naar de projectmap:

```bash
cd "/Users/thijszoontjes/Projects/youtube-kanaal"
```

Activeer daarna de virtual environment:

```bash
source .venv/bin/activate
```

### 4 Shorts uploaden met een datum erbij

Deze command maakt 4 Shorts en plant ze op YouTube voor de gekozen datum en tijden:

```bash
.venv/bin/python -m youtube_kanaal make-short-schedule --date 2026-05-18 --times "10:00,13:00,15:00,19:00"
```

### Tijd voor de lange video aanpassen

Dit past de uploadtijd alleen aan voor deze terminal-sessie:

```bash
export LONG_PUBLISH_TIME="17:00"
```

Wil je het blijvend aanpassen, zet dit in `.env`:

```dotenv
LONG_PUBLISH_TIME=17:00
```

### Vandaag 1 lange video uploaden

Gebruik `--for today` als de lange video vandaag live moet:

```bash
.venv/bin/python -m youtube_kanaal generate-and-schedule --for today --upload
```

### Shorts en Reels tegelijk uploaden

Deze command maakt 4 Shorts, plant ze op YouTube, en uploadt dezelfde video's meteen als Instagram Reels:

```bash
.venv/bin/python -m youtube_kanaal make-short-schedule-reels --date 2026-05-18 --times "11:00,13:00,15:00,19:00"
```

## Snelste copy-paste set

Windows:

```powershell
ollama pull llama3.2:3b
$env:LONG_PUBLISH_TIME="17:00"
.\.venv\Scripts\python -m youtube_kanaal make-short-schedule --date 2026-05-18 --times "10:00,13:00,15:00,19:00"
.\.venv\Scripts\python -m youtube_kanaal generate-and-schedule --for today --upload
```

Mac:

```bash
ollama pull llama3.2:3b
export LONG_PUBLISH_TIME="17:00"
.venv/bin/python -m youtube_kanaal make-short-schedule --date 2026-05-18 --times "10:00,13:00,15:00,19:00"
.venv/bin/python -m youtube_kanaal generate-and-schedule --for today --upload
```
