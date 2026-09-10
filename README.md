# autopyrma

Minimal Python PDF signer compatible with [Cliente @Firma](https://github.com/ctt-gob-es/clienteafirma)
(Afirma / AutoFirma). It produces a **PAdES-Basic** signature
(`SubFilter=adbe.pkcs7.detached`, incremental update, approval signature) with the
same **visible stamp on the last page** that Afirma generates by default:

```
Firmado por <certificate CN>
Fecha: dd/mm/yyyy HH:MM:SS TZ
[Motivo: ...]
[Lugar de firma: ...]
```

Only a small subset of the Java project is replicated: single-signer PDF signing
with a visible AcroForm widget. No timestamping (TSA), no PAdES-BES policies,
no co-signing — yet.

## Requirements

- Python ≥ 3.13
- Dependencies (installed automatically): `pyhanko`, `cryptography`,
  `asn1crypto`, `keyring`, `platformdirs`

## Install

```bash
# with uv (recommended)
uv sync        # or: uv pip install -e .
# with pip
pip install -e .
```

After installing, the `autopyrma` command is available
(`python -m autopyrma` works too).

## Preparing your certificate (.p12 / .pfx)

Signing needs a **PKCS#12 bundle**: one password-protected file holding your
private key + signing certificate (usually with the CA chain). `.p12` and
`.pfx` are the same format — either extension works.

**Export an existing certificate** (FNMT, Camerfirma, …) from where its
private key lives:

- Firefox: Settings → Privacy & Security → Certificates → Your Certificates → Backup.
- Chrome/Edge on Windows: `chrome://settings/certificates` → export *with* private key.
- Windows certificate store: `certmgr.msc` → Personal → Export → *Yes, export the private key* → PKCS#12.
- DNIe / smartcards: the key normally **cannot** be exported — that needs a
  PKCS#11 code path, which autopyrma does not support yet.

**Bundle separate PEM files** with OpenSSL:

```bash
openssl pkcs12 -export -inkey key.pem -in cert.pem \
  -certfile chain.pem -out cert.pfx
# the export password you set here is the password autopyrma will ask for
```

**Just testing?** Create a self-signed fixture:

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=Test Firmante/O=autopyrma"
openssl pkcs12 -export -inkey key.pem -in cert.pem \
  -out test.p12 -passout pass:1234
```

Sanity check: `openssl pkcs12 -in cert.pfx -info -noout` (lists key + certs).

## Quick start (explicit flags)

```bash
autopyrma --p12 cert.pfx --in document.pdf --out document-signed.pdf
# → prompts: P12 password:   (typing is hidden, nothing lands in shell history)
```

With options:

```bash
autopyrma --p12 cert.pfx --in in.pdf --out signed.pdf \
  --reason "Aprobado" --location "Madrid" --contact "user@example.com" \
  --box 100 100 200 200 --page -1 --md sha512
```

| Flag | Meaning | Default |
|---|---|---|
| `--p12` | PKCS#12 file | (see resolution below) |
| `--password` | P12 password (avoid: stays in shell history) | prompt / env / keyring |
| `--reason` / `--location` / `--contact` | `/Reason`, `/Location`, `/ContactInfo` entries | none |
| `--box LLX LLY URX URY` | stamp rectangle, points, bottom-left origin | `100 100 200 200` |
| `--page` | 0-based page for the stamp | `-1` (last page, like Afirma) |
| `--field` | AcroForm field name | `Signature1` |
| `--md` | digest: `sha256` / `sha384` / `sha512` | `sha512` (Afirma default) |

## Recommended setup: config file + OS keyring

Typing `--p12 …/cert.pfx` and the password every time is tedious and insecure
in history. The recommended split:

- **Certificate path and defaults** → config file (plain TOML, per-OS location).
- **Password** → OS keyring (encrypted by the OS, never in a file).

One-time setup:

```bash
# 1. Save the cert path and your usual details during your first signing
#    (only the --p12/--reason/--location/--contact flags you pass are stored)
autopyrma --p12 ~/certs/mi-cert.p12 --reason "Aprobado" --location "Madrid" \
  --in document.pdf --out document-signed.pdf --save-config
# → Saved location, p12_path, reason to ~/.config/autopyrma/config.toml

# 2. Store the password once (hidden prompt, OS keyring)
autopyrma --p12 ~/certs/mi-cert.p12 --store-password
# → Password stored in keyring (autopyrma/~/certs/mi-cert.p12).
```

Everyday use from then on:

```bash
autopyrma --in document.pdf --out document-signed.pdf
```

Forget the password with `autopyrma --forget-password`
(uses the configured `--p12` to find the entry).

### Where the config file lives

| OS | Path |
|---|---|
| Linux | `~/.config/autopyrma/config.toml` |
| macOS | `~/Library/Application Support/autopyrma/config.toml` |
| Windows | `%APPDATA%\autopyrma\config.toml` |

Example content (never contains the password):

```toml
[signer]
p12_path = "/home/user/certs/mi-cert.p12"
reason = "Aprobado"
location = "Madrid"
contact = "user@example.com"
```

The file is created with mode `0600`. You can also edit it by hand.

### How the password is found (precedence)

1. `--password` flag (convenient, but visible in history — testing only)
2. `$AUTOPYRMA_P12_PASSWORD` env var (CI/headless)
3. OS keyring: Windows Credential Manager / macOS Keychain / Linux Secret Service
4. Interactive hidden prompt (TTY only; otherwise a clean error, no hang)

The P12 path is resolved as: `--p12` > `$AUTOPYRMA_P12` > config file.

### Keyring notes

- Desktops "just work"; the first use may show an OS dialog asking to allow
  access — approve once.
- Headless/minimal Linux often has **no** Secret Service daemon, so there is no
  usable backend. You'll get a clear error telling you to use the env var or
  the prompt instead (install GNOME Keyring/KWallet to enable it).
- One entry per certificate (keyed by P12 path), so several certs can coexist.

## Verifying a signed PDF

Open it in AutoFirma or Adobe Reader: one valid signature, visible stamp on the
last page. Programmatically:

```python
from autopyrma.pades import sign_pdf
sign_pdf("cert.pfx", "password", "in.pdf", "signed.pdf",
         reason="Aprobado", location="Madrid")
```

## Security notes

- Prefer the keyring over `--password` / env vars on shared machines (`ps`
  and shell history can leak them).
- The config file holds no secrets; still keep `~/.config` (or equivalent)
  private, as usual.
- A keyring protects against disk theft, backups, accidental `git push`, and
  shoulder-surfing — not against malware already running as your user.

## Limitations (v1)

- No TSA timestamping, no PAdES-BES policy OIDs, no co-/counter-signatures.
- No handwritten rubric image (Afirma `signatureRubricImage`); text stamp only.
- No PKCS#11 / smartcard / DNIe support (exportable P12 only).
- Default font is pyHanko's bundled font rather than Afirma's Courier —
  same text content and position, slightly different metrics.
