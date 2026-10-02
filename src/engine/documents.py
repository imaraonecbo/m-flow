from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import textwrap
from typing import Any

TWOPLACES = Decimal("0.01")


def money(value: str | int | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class TradeTerms:
    opportunity_id: str
    amount: Decimal
    margin: Decimal
    settlement_date: str
    buyer_id: str
    supplier_id: str
    capital_provider_id: str
    currency: str
    cogs: Decimal
    financing_cost: Decimal
    platform_fee: Decimal

    def normalized(self) -> dict[str, str]:
        return {
            "opportunityId": self.opportunity_id,
            "amount": str(money(self.amount)),
            "margin": str(money(self.margin)),
            "settlementDate": self.settlement_date,
            "buyerId": self.buyer_id,
            "supplierId": self.supplier_id,
            "capitalProviderId": self.capital_provider_id,
            "currency": self.currency.upper(),
            "cogs": str(money(self.cogs)),
            "financingCost": str(money(self.financing_cost)),
            "platformFee": str(money(self.platform_fee)),
        }

    @property
    def terms_hash(self) -> str:
        return sha256_hex(canonical_json(self.normalized()))


BASE_DISCLAIMER = (
    "This template records agreed commercial terms and cryptographic bindings. "
    "Legal enforceability depends on applicable law, proper execution, and review by qualified counsel. "
    "It is not a funding commitment or guarantee of payment."
)


def _header(title: str, terms: TradeTerms) -> str:
    return "\n".join([
        f"# {title}",
        "",
        f"Opportunity ID: {terms.opportunity_id}",
        f"Trade Terms SHA-256: {terms.terms_hash}",
        f"Settlement Date: {terms.settlement_date}",
        f"Amount: {terms.amount} {terms.currency.upper()}",
        "",
    ])


def build_tri_party_agreement(terms: TradeTerms) -> str:
    return _header("M-FLOW Tri-Party Trade Financing Agreement", terms) + "\n".join([
        "## Parties",
        f"- Buyer: `{terms.buyer_id}`",
        f"- Supplier: `{terms.supplier_id}`",
        f"- Capital Provider: `{terms.capital_provider_id}`",
        "",
        "## Commercial Terms",
        f"- Transaction amount: `{money(terms.amount)} {terms.currency.upper()}`",
        f"- Gross margin: `{money(terms.margin)} {terms.currency.upper()}`",
        f"- COGS: `{money(terms.cogs)} {terms.currency.upper()}`",
        f"- Financing cost: `{money(terms.financing_cost)} {terms.currency.upper()}`",
        f"- M-FLOW platform fee: `{money(terms.platform_fee)} {terms.currency.upper()}`",
        "",
        "## Conditions",
        "1. Parties must independently verify the underlying trade, identities, authority, and payment instructions.",
        "2. No principal release occurs unless M-FLOW's applicable live settlement gate is satisfied.",
        "3. Changes to the cryptographically bound terms require a new document hash and fresh authorization.",
        "",
        "## Signatures",
        "Buyer authorized signatory: ____________________  Date: __________",
        "Supplier authorized signatory: ________________  Date: __________",
        "Capital Provider authorized signatory: __________  Date: __________",
        "",
        f"{BASE_DISCLAIMER}",
    ])


def build_irrevocable_assignment(terms: TradeTerms) -> str:
    return _header("M-FLOW Irrevocable Assignment of Receivables", terms) + "\n".join([
        "## Assignment",
        f"The Supplier (`{terms.supplier_id}`) assigns the specified receivable arising from opportunity `{terms.opportunity_id}` to the designated settlement destination approved under the executed financing arrangement.",
        "",
        "## Payment Direction",
        "The Buyer must acknowledge the payment redirection and independently verify the notice of assignment before settlement.",
        "",
        "## Controls",
        f"- Bound trade-terms hash: `{terms.terms_hash}`",
        "- Any payment instruction change invalidates the prior authorization and requires fresh verification.",
        "",
        "## Signatures",
        "Supplier authorized signatory: ________________  Date: __________",
        "Buyer acknowledgment: _________________________  Date: __________",
        "Settlement agent / authorized intermediary: _____  Date: __________",
        "",
        f"{BASE_DISCLAIMER}",
    ])


def build_platform_services_agreement(terms: TradeTerms) -> str:
    return _header("M-FLOW Platform Services & Origination Fee Agreement", terms) + "\n".join([
        "## Services",
        "M-FLOW provides opportunity evaluation, evidence workflow, transaction documentation, capital-provider matching, audit controls, and authorized settlement orchestration as agreed by the parties.",
        "",
        "## Fee",
        f"The recorded platform fee for this transaction is `{money(terms.platform_fee)} {terms.currency.upper()}`.",
        "The applicable fee rate and any capital-spread participation must be expressly accepted by the contracting parties and recorded in the ledger before execution.",
        "",
        "## Revenue Protection",
        "Accrued platform fees are separately recorded. Where contractually required, fee settlement must be confirmed before principal release is authorized.",
        "",
        "## Signatures",
        "Buyer authorized signatory: ____________________  Date: __________",
        "Supplier authorized signatory: ________________  Date: __________",
        "Capital Provider authorized signatory: __________  Date: __________",
        "",
        f"{BASE_DISCLAIMER}",
    ])


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_pdf(markdown_text: str) -> bytes:
    """Generate a dependency-free, single-page-per-section PDF container."""
    lines: list[str] = []
    for paragraph in markdown_text.splitlines():
        clean = paragraph.replace("`", "").replace("#", "").replace("**", "")
        wrapped = textwrap.wrap(clean, width=92) or [""]
        lines.extend(wrapped)
    # Keep output bounded and deterministic enough for hashing after generation.
    lines = lines[:55]
    content_lines = [
        "BT",
        "/F1 9 Tf",
        "50 780 Td",
    ]
    for i, line in enumerate(lines):
        if i:
            content_lines.append("0 -13 Td")
        content_lines.append(f"({_pdf_escape(line)}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objects.append(b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n")
    objects.append(b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    objects.append(b"5 0 obj\n<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream\nendobj\n")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(out)


def generate_trade_documents(terms: TradeTerms) -> dict[str, dict[str, str | bytes]]:
    documents = {
        "TRI_PARTY_TRADE_FINANCING": build_tri_party_agreement(terms),
        "IRREVOCABLE_ASSIGNMENT": build_irrevocable_assignment(terms),
        "PLATFORM_SERVICES_ORIGINATION": build_platform_services_agreement(terms),
    }
    return {
        kind: {
            "markdown": body,
            "pdf": render_pdf(body),
            "sha256": sha256_hex(body),
            "termsHash": terms.terms_hash,
        }
        for kind, body in documents.items()
    }
