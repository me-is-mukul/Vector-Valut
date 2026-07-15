# Privacy and safety

## Data handling

| Data | Where it lives | Leaves the device? |
|---|---|---|
| Your documents | Where they already were, plus filed **copies** in `~/AI Library` | Never |
| Extracted text, chunks, embeddings | `%LOCALAPPDATA%\osdc` (SQLite + vector store) | Never |
| Photos | Indexed **in place** — never moved, copied or uploaded | Never |
| Questions you ask | Sent to Ollama on `127.0.0.1` only | Never |
| Settings, logs | `%LOCALAPPDATA%\osdc` | Never |

No account, no telemetry, no analytics, no crash reporting. The only network traffic the
app ever generates is the one-time download of its own models during setup.

## Permissions and scope

- **Reads**: only folders you explicitly point it at (dropped into chat, or added as
  watched folders in Settings).
- **Writes**: only inside the library root (`~/AI Library`) and its own data directory.
  The default file action is **copy**, so originals stay untouched until you opt into move.
- **Executes**: nothing. The LLM's output is data (a filing plan), validated against a
  schema and executed by a deterministic engine that cannot leave the library root.

## Safety mechanisms

- **Write-ahead move log** — every file operation is journaled *before* it happens; undo is
  always available and refuses to destroy the only remaining copy of anything.
- **Review queue** — anything below the measured confidence thresholds waits for a human
  instead of being auto-filed.
- **Grounded answers only** — if retrieval finds nothing relevant, the model is never
  called, so it cannot invent the contents of your medical or financial records.
- **Everything reversible, nothing overwritten** — name collisions get `(1)` suffixes.

## Limitations and residual risks

- Misclassification within the library is possible (wrong subject folder). Mitigated by
  copy-by-default, the review queue, per-file reasons in the plan preview, and undo — but
  a user who blind-approves large plans can still end up with misfiled copies.
- The local database contains extracted text of everything indexed. It is not encrypted at
  rest beyond OS file permissions — same threat model as the original documents themselves.
  Anyone with access to your Windows account can read it, as they could the originals.
- Model downloads come from Hugging Face and Ollama's registry over HTTPS; supply-chain
  trust in those registries is assumed, as with any model-based app.
- The app trusts its watched folders: a malicious filename cannot become a command (paths
  are data end-to-end), but indexing hostile *file contents* into the library is possible —
  the same risk as saving that file anywhere else.
