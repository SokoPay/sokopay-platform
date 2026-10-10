"""
The AI gateway's data controls: redaction both ways, k-anonymity, aggregate-only tools,
role/rate/budget limits, off by default, and every call logged. The Anthropic client is
replaced by a fake that records exactly what would have been sent.
"""

import json
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache

from apps.common.models import AuditEvent
from apps.insights.ai import gateway, redact, tools
from apps.insights.models import AiBriefing, AiInteraction
from apps.kyc.models import KycProfile
from apps.payments.models import Payment
from apps.portal.tests.helpers import PASSWORD, login_verified

User = get_user_model()
pytestmark = pytest.mark.django_db
PHONE = "+233244000201"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.AI_PROVIDER = "anthropic"
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.AI_CHAT_ENABLED = True
    settings.AI_MODEL = "claude-opus-5-5"
    settings.AI_CHAT_PER_HOUR = 20
    settings.AI_DAILY_TOKEN_BUDGET = 300_000
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def analyst(db):
    u = User.objects.create_user(phone="+233200000206", full_name="Yaa Finance", password=PASSWORD, user_type="staff")
    u.groups.add(Group.objects.get(name="finance"))
    return u


@pytest.fixture
def customers(db):
    kofi = User.objects.create_user(phone=PHONE, full_name="Kofi Asante")
    KycProfile.objects.create(user=kofi, tier=1, ghana_card_number="GHA-123456789-0", ghana_card_hash="h1",
                              verified_name="KOFI ASANTE")
    for i in range(3):
        Payment.objects.create(reference=f"SP-AI{i:07d}", purpose="bill", status="succeeded", user=kofi,
                               amount_minor=123_45, total_minor=123_45, network="mtn", rail="mock")
    return kofi


class FakeClient:
    """Records every request; replays scripted responses."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []
        self.messages = self

    def create(self, **kwargs):
        self.sent.append(json.loads(json.dumps(kwargs, default=str)))
        return self.script.pop(0)


def _resp(blocks, stop="end_turn", tokens=(100, 50)):
    return SimpleNamespace(content=blocks, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]))


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool(name, args=None, id_="tu_1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args or {})


# --- redaction ------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "call +233244000201 now", "his number is 0244000201", "233 24 400 0201", "card GHA-123456789-0",
    "mail kofi@example.com", "ref SP-7KQ2MX9D4T", "id 0f8fad5b-d9cb-469f-a165-70867728950e",
    "wallet 7629358578", "passport G1234567",
])
def test_scrub_removes_identifiers(text):
    clean, found = redact.scrub(text)
    assert found and "removed]" in clean
    for secret in ("244000201", "123456789", "kofi@", "7KQ2MX9D4T", "0f8fad5b", "7629358578", "1234567"):
        assert secret not in clean


def test_scrub_keeps_amounts_dates_and_percentages():
    text = "Volume was GH₵ 12,756.50 on 2026-10-08, up 12.5% (1,234 transactions)."
    assert redact.scrub(text) == (text, [])


def test_k_anonymity():
    assert redact.k_anon(0) == 0 and redact.k_anon(3) == "<5" and redact.k_anon(5) == 5


def test_tools_never_return_personal_data(customers):
    blob = json.dumps({name: tools.run(name, {"days": 7}) for name in tools.TOOLS}, default=str)
    for secret in ("244000201", "Kofi", "KOFI", "Asante", "GHA-", "SP-AI", str(customers.pk)):
        assert secret not in blob
    overview = tools.run("platform_overview", {})
    assert overview["users"]["customers"] == "<5"
    assert overview["today_by_status"]["completed"] == {"count": "<5", "value": "suppressed"}


def test_tool_inputs_are_clamped():
    assert len(tools.run("daily_volumes", {"days": 100000})["days"]) == 90
    assert tools.run("not_a_tool", {}) == {"error": "Unknown tool."}


# --- gateway --------------------------------------------------------------------------------
def test_off_by_default(settings, analyst):
    settings.AI_PROVIDER = "off"
    with pytest.raises(gateway.AiUnavailable):
        gateway.ask(analyst, "How was yesterday?")


def test_only_allowed_roles(analyst):
    support = User.objects.create_user(phone="+233200000208", password=PASSWORD, user_type="staff")
    support.groups.add(Group.objects.get(name="support"))
    customer = User.objects.create_user(phone="+233244000999")
    assert gateway.user_allowed(analyst) and not gateway.user_allowed(support) and not gateway.user_allowed(customer)
    with pytest.raises(gateway.AiUnavailable, match="role"):
        gateway.ask(support, "How was yesterday?")


def test_question_tools_and_answer_are_redacted_and_logged(monkeypatch, analyst, customers):
    fake = FakeClient([
        _resp([_text("Let me look."), _tool("platform_overview")], stop="tool_use"),
        _resp([_text("Completed volume is steady. Customer +233244000201 asked about it.")]),
    ])
    monkeypatch.setattr(gateway, "_client", lambda: fake)
    out = gateway.ask(analyst, "Why did 0244000201 complain? How is volume today?")
    sent = json.dumps(fake.sent)
    assert "244000201" not in sent and "Kofi" not in sent and "GHA-" not in sent    # nothing personal left SokoPay
    assert "[phone removed]" in fake.sent[0]["messages"][0]["content"]
    assert fake.sent[0]["model"] == "claude-opus-5-5" and fake.sent[0]["tools"]
    assert "244000201" not in out["answer"] and "[phone removed]" in out["answer"]   # scrubbed on the way back
    assert out["tools"] == ["platform_overview"] and "phone" in out["redactions"]
    log = AiInteraction.objects.get()
    assert log.user == analyst and log.status == "ok" and "244000201" not in log.question
    assert log.input_tokens == 200 and log.output_tokens == 100
    assert AuditEvent.objects.filter(action="ai.chat").exists()


def test_rate_limit_and_budget(monkeypatch, settings, analyst):
    settings.AI_CHAT_PER_HOUR = 1
    monkeypatch.setattr(gateway, "_client", lambda: FakeClient([_resp([_text("ok")])]))
    gateway.ask(analyst, "How was today?")
    with pytest.raises(gateway.AiUnavailable, match="last hour"):
        gateway.ask(analyst, "And now?")
    cache.clear()
    settings.AI_CHAT_PER_HOUR = 20
    settings.AI_DAILY_TOKEN_BUDGET = 100
    with pytest.raises(gateway.AiUnavailable, match="budget"):
        gateway.ask(analyst, "And now?")


def test_api_errors_are_hidden_and_logged(monkeypatch, analyst):
    class Boom:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise RuntimeError("secret internal detail")

    monkeypatch.setattr(gateway, "_client", lambda: Boom())
    with pytest.raises(gateway.AiUnavailable) as exc:
        gateway.ask(analyst, "How was today?")
    assert "secret" not in str(exc.value)
    assert AiInteraction.objects.get().status == "error"


def test_tool_rounds_are_bounded(monkeypatch, settings, analyst):
    settings.AI_MAX_TOOL_ROUNDS = 1
    fake = FakeClient([_resp([_tool("daily_volumes", id_=f"t{i}")], stop="tool_use") for i in range(3)])
    monkeypatch.setattr(gateway, "_client", lambda: fake)
    with pytest.raises(gateway.AiUnavailable, match="too many steps"):
        gateway.ask(analyst, "Loop forever")
    assert len(fake.sent) == 2


def test_daily_briefing_sends_only_aggregates(monkeypatch, analyst, customers):
    fake = FakeClient([_resp([_text("Headline: a quiet day.")])])
    monkeypatch.setattr(gateway, "_client", lambda: fake)
    b = gateway.daily_briefing(user=analyst)
    assert isinstance(b, AiBriefing) and b.text.startswith("Headline")
    sent = json.dumps(fake.sent)
    assert "244000201" not in sent and "Kofi" not in sent and "tools" not in fake.sent[0]


# --- portal ---------------------------------------------------------------------------------
def test_insights_page_and_ask_endpoint(monkeypatch, analyst):
    monkeypatch.setattr(gateway, "_client", lambda: FakeClient([_resp([_text("Volumes are normal.")])]))
    c = login_verified(analyst.phone)
    page = c.get("/dashboard/admin/insights/")
    assert page.status_code == 200 and b"Ask the AI" in page.content and b"How customer data is protected" in page.content
    r = c.post("/dashboard/admin/insights/ask/", {"question": "How are volumes?"})
    assert r.status_code == 200 and b"Volumes are normal." in r.content


def test_insights_page_explains_when_ai_is_off(settings, analyst):
    settings.AI_PROVIDER = "off"
    page = login_verified(analyst.phone).get("/dashboard/admin/insights/")
    assert b"switched off" in page.content and b"Ask the AI" in page.content
