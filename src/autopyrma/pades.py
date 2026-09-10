"""Minimal PAdES PDF signer compatible with Cliente @Firma (Afirma).

Replicates the default behaviour of
``es.gob.afirma.signers.pades.AOPDFSigner`` for the common case:

* ``SubFilter=adbe.pkcs7.detached`` (PAdES-Basic), incremental update.
* Approval (non-certified) signature over the whole document.
* Visible AcroForm signature widget on the last page by default, with the
  same default stamp text as Afirma ("Firmado por <CN> / Fecha: ...").

Java references:
* ``afirma-crypto-pdf/.../pades/AOPDFSigner.java`` (default SHA512withRSA)
* ``afirma-crypto-pdf/.../pades/PdfSessionManager.java:getDefaultLayer2Text``
* ``afirma-crypto-pdf-common/.../common/PdfExtraParams.java``
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from asn1crypto.algos import SignedDigestAlgorithm
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.text import TextBoxStyle
from pyhanko.sign import fields
from pyhanko.sign.signers import PdfSignatureMetadata, PdfSigner
from pyhanko.sign.signers.pdf_cms import SimpleSigner
from pyhanko.stamp import TextStampStyle

# Afirma default signature algorithm (AOSignConstants.DEFAULT_SIGN_ALGO).
DEFAULT_MD = "sha512"
# Afirma example rect; origin is bottom-left in points.
DEFAULT_BOX = (100.0, 100.0, 200.0, 200.0)
DEFAULT_FIELD_NAME = "Signature1"
AFIRMA_DATE_FORMAT = "%d/%m/%Y %H:%M:%S %Z"


def _signer_cn(signer: SimpleSigner) -> str:
    """Extract the Common Name (CN) of the signing certificate."""
    try:
        subject = signer.signing_cert.native["tbs_certificate"]["subject"]
        for rdn in subject:
            for attr in rdn:
                if attr["type"] == "common_name":
                    return str(attr["value"])
        return str(subject)
    except Exception:
        return "Firmante"


def _signature_mechanism(md: str, signer: SimpleSigner) -> SignedDigestAlgorithm:
    """Build e.g. sha512_rsa / sha512_ecdsa matching the key type."""
    key_type = signer.signing_key.algorithm
    suffix = "ecdsa" if key_type == "ec" else "rsa"
    return SignedDigestAlgorithm({"algorithm": f"{md}_{suffix}"})


def build_stamp_text(
    cn: str,
    when: datetime,
    reason: str | None = None,
    location: str | None = None,
) -> str:
    """Afirma ``getDefaultLayer2Text`` equivalent (already substituted)."""
    lines = [
        f"Firmado por {cn}",
        f"Fecha: {when.strftime(AFIRMA_DATE_FORMAT)}",
    ]
    if reason:
        lines.append(f"Motivo: {reason}")
    if location:
        lines.append(f"Lugar de firma: {location}")
    return "\n".join(lines)


def _courier_style(font_size: int = 10) -> TextBoxStyle:
    # Default pyHanko font (no uharfbuzz/opentype dependency).
    # Afirma uses Courier; pyHanko default is close enough for v1.
    return TextBoxStyle(font_size=font_size)


def sign_pdf(
    p12_path: str | Path,
    password: str | bytes | None,
    src_pdf: str | Path,
    dst_pdf: str | Path,
    *,
    reason: str | None = None,
    location: str | None = None,
    contact: str | None = None,
    box: tuple[float, float, float, float] = DEFAULT_BOX,
    page: int = -1,
    field_name: str = DEFAULT_FIELD_NAME,
    md_algorithm: str = DEFAULT_MD,
    stamp_text: str | None = None,
    timestamp_format: str = AFIRMA_DATE_FORMAT,
) -> Path:
    """Sign ``src_pdf`` with the PKCS#12 bundle at ``p12_path``.

    :param p12_path: path to ``.p12``/``.pfx`` with key + chain.
    :param password: P12 password (str or bytes), or None if unprotected.
    :param src_pdf: input PDF.
    :param dst_pdf: output (signed) PDF.
    :param reason: ``/Reason`` dict entry (Afirma ``signReason``).
    :param location: ``/Location`` dict entry (``signatureProductionCity``).
    :param contact: ``/ContactInfo`` dict entry (``signerContact``).
    :param box: ``(llx, lly, urx, ury)`` in points, bottom-left origin.
    :param page: 0-based page index; ``-1`` = last page (Afirma default).
    :param field_name: AcroForm signature field name to create.
    :param md_algorithm: e.g. ``sha256``/``sha384``/``sha512`` (Afirma: sha512).
    :param stamp_text: override stamp template; may use ``%(signer)s``
        and ``%(ts)s`` placeholders (pyHanko style).
    :param timestamp_format: strftime format for ``%(ts)s``.
    :returns: path of the signed PDF.
    """
    p12_path = Path(p12_path)
    src_pdf = Path(src_pdf)
    dst_pdf = Path(dst_pdf)
    if isinstance(password, str):
        password = password.encode()

    signer = SimpleSigner.load_pkcs12(pfx_file=str(p12_path), passphrase=password)
    if signer is None:
        raise ValueError(f"Could not load PKCS#12 key material from {p12_path}")
    signer._signature_mechanism = _signature_mechanism(md_algorithm, signer)

    cn = _signer_cn(signer)
    now = datetime.now().astimezone()
    text = stamp_text or build_stamp_text(cn, now, reason, location)

    with open(src_pdf, "rb") as f:
        reader = PdfFileReader(f)
        n_pages = len(reader.root["/Pages"]["/Kids"])
        target = n_pages + page if page < 0 else page
        if not 0 <= target < n_pages:
            raise ValueError(f"page {page} out of range for {n_pages}-page PDF")

        writer = IncrementalPdfFileWriter(f)
        meta = PdfSignatureMetadata(
            field_name=field_name,
            md_algorithm=md_algorithm,
            location=location,
            reason=reason,
            contact_info=contact,
            name=cn,
            subfilter=fields.SigSeedSubFilter.ADOBE_PKCS7_DETACHED,
        )
        stamp_style = TextStampStyle(
            stamp_text=text.replace("%", "%%"),  # literal text, no interpolation
            timestamp_format=timestamp_format,
            text_box_style=_courier_style(),
            border_width=1,
            background_opacity=0.0,
        )
        # Neutral appearance_text_params: text is pre-rendered, but pyHanko
        # still requires ts/signer keys for its default template machinery.
        appearance_params = {"signer": cn, "ts": now}
        field_spec = fields.SigFieldSpec(
            sig_field_name=field_name,
            on_page=target,
            box=tuple(float(v) for v in box),  # type: ignore[arg-type]
        )
        pdf_signer = PdfSigner(
            meta, signer=signer, stamp_style=stamp_style, new_field_spec=field_spec
        )
        with open(dst_pdf, "wb") as out:
            pdf_signer.sign_pdf(
                writer,
                appearance_text_params=appearance_params,
                output=out,
            )
    return dst_pdf
