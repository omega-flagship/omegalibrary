"""Tiny synthetic PDFs, written byte by byte at test time (no real document is used in the tests)."""

import io


def _pdf(objects):
    """objects: list of bytes bodies for objects 1..n; object 1 must be the catalog."""
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def _stream(content, extra=b""):
    return b"<< /Length %d %s>>\nstream\n" % (len(content), extra) + content + b"\nendstream"


def text_pdf(pages):
    """One page per string, Helvetica text."""
    n = len(pages)
    # 1 catalog, 2 pages, 3 font, then (page, content) pairs
    kids = b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(n))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % n,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")
        content = b"BT /F1 12 Tf 72 720 Td (" + safe + b") Tj ET"
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    b"/Contents %d 0 R >>" % (5 + 2 * i))
        objs.append(_stream(content))
    return _pdf(objs)


def image_only_pdf(pages=2):
    """Pages that draw a 1x1 grey image and carry no text layer at all (what a scan looks like to pypdf)."""
    kids = b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(pages))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % pages,
            _stream(b"\x80", b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
                              b"/BitsPerComponent 8 ")]
    for i in range(pages):
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im1 3 0 R >> >> "
                    b"/Contents %d 0 R >>" % (5 + 2 * i))
        objs.append(_stream(b"q 500 0 0 700 50 50 cm /Im1 Do Q"))
    return _pdf(objs)


def encrypted_pdf(pages, user_password="secret"):
    """A text PDF encrypted with a user password (RC4-128, which pypdf writes without any crypto library)."""
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(text_pdf(pages)))
    writer = pypdf.PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    writer.encrypt(user_password=user_password, owner_password="owner-" + user_password, algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def malformed_pdf():
    """A PDF header followed by nothing a reader can use."""
    return b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 9 0 R\nthis file was cut off here"


def not_a_pdf():
    return b"\x89PNG\r\n\x1a\n not a pdf at all"
