# Local privacy policy

KPI Bridge is a **local** tool. In practice:

- **Nothing is sent to a third party.** Files, excerpts, requests and results never leave the computer. There is no
  telemetry, no usage statistics, no automatic error report and no cloud API.
- **Local AI.** Requests are interpreted by Ollama on this machine. The model receives the table schema, bounded
  profiles and at most three short samples per text column, never the whole file.
- **Network.** The internet is used only at install time (dependencies, Tesseract, Ollama, model) and for an
  explicitly requested update (`kpi update`). Day-to-day operation is offline.
- **Storage.** Data stays in `~/.kpi-bridge/data` (or `KPI_DATA_DIR`) until the project is deleted. The application
  does not encrypt it: protection also depends on disk encryption.
- **Deletion.** "Delete project" removes the sources, derived data, results, temporary exports and metadata. It is
  not a forensic wipe; backups you made (`kpi backup`) or those of your system are not affected.
- **Logs.** Technical logs (`data/logs`) contain no document content, tokens or cookies.
- **Demo data.** The bundled example files are entirely synthetic.
