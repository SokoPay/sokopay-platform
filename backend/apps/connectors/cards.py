"""
Card-scheme connectors: Visa, Mastercard, and gh-link (Ghana's domestic card scheme,
run by GhIPSS).

Two uses:
  * Acquiring — taking a card payment through a hosted page or hosted fields, so the
    card number (PAN) never touches SokoPay. Licence: CARD_ACQUIRING (PSP Medium).
  * Push-to-card — paying out to a card (Visa Direct / Mastercard Send) using a
    scheme token. Licence: PAYMENT_PROCESSING (PSP Enhanced).

In practice SokoPay reaches the schemes through an acquiring bank or processor, not
directly; these placeholders mark where that integration goes.

PCI DSS: keep it this way — only tokens and hosted sessions, never raw card data.
"""

from __future__ import annotations

from .placeholder import PlaceholderCardScheme

_PCI = "Integration must use hosted fields/tokens only (no PAN in SokoPay systems)."


class VisaConnector(PlaceholderCardScheme):
    key = "visa"
    display_name = "Visa — acquiring & Visa Direct (push-to-card)"
    regulator = "Card scheme (Visa); acquiring bank licensed by Bank of Ghana"
    go_live = (f"Acquiring-bank or processor agreement; Visa Direct onboarding for "
               f"push-to-card; scheme certification. {_PCI} [VERIFY]")


class MastercardConnector(PlaceholderCardScheme):
    key = "mastercard"
    display_name = "Mastercard — acquiring & Mastercard Send (push-to-card)"
    regulator = "Card scheme (Mastercard); acquiring bank licensed by Bank of Ghana"
    go_live = (f"Acquiring-bank or processor agreement; Mastercard Send onboarding; "
               f"scheme certification. {_PCI} [VERIFY]")


class GhLinkConnector(PlaceholderCardScheme):
    key = "ghlink"
    display_name = "gh-link (GhIPSS domestic card scheme)"
    regulator = "Bank of Ghana (GhIPSS)"
    go_live = f"gh-link acceptance through an acquiring participant; certification. {_PCI} [VERIFY]"
